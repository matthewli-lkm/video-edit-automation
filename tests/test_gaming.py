from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from video_edit_automation.application.gaming import GamingHighlightService, HighlightScorer
from video_edit_automation.domain.gaming import (
    GameContext,
    HighlightAnalysis,
    HighlightCandidate,
    HighlightSignal,
    HighlightSignalType,
)
from video_edit_automation.domain.models import MediaAsset
from video_edit_automation.infrastructure.game_profiles import BUILTIN_GAME_PROFILES


class FakeLeagueAnalyzer:
    name = "fake-league-analyzer"

    def __init__(self, available: bool = True) -> None:
        self.is_available = available

    def available(self) -> bool:
        return self.is_available

    def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis:
        return HighlightAnalysis(
            analyzer=self.name,
            asset_id=asset.id,
            source_fingerprint=asset.source_fingerprint,
            game=game,
            sampled_frame_count=3,
            audio_peak_count=1,
            signals=[
                HighlightSignal(
                    id="auto-kill-1",
                    asset_id=asset.id,
                    timestamp_seconds=5,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="champion_kill",
                    confidence=0.92,
                    source="test:league-ocr",
                    metadata={"raw_text": "PlayerOne has slain PlayerTwo"},
                )
            ],
        )


def _asset(duration_seconds: float = 120) -> MediaAsset:
    return MediaAsset(
        project_id=uuid4(),
        source_path=Path("/tmp/gameplay.mkv"),
        source_fingerprint="game",
        size_bytes=100,
        modified_at_ns=1,
        duration_seconds=duration_seconds,
        width=1920,
        height=1080,
        frame_rate=60,
        has_audio=True,
    )


def test_fps_signals_are_weighted_and_nearby_windows_are_merged() -> None:
    asset = _asset()
    signals = [
        HighlightSignal(
            id="kill-1",
            asset_id=asset.id,
            timestamp_seconds=40,
            signal_type=HighlightSignalType.GAME_EVENT,
            event_name="kill",
        ),
        HighlightSignal(
            id="reaction-1",
            asset_id=asset.id,
            timestamp_seconds=43,
            signal_type=HighlightSignalType.MICROPHONE_REACTION,
            source_audio_stream_index=2,
            confidence=0.8,
        ),
    ]
    candidates = HighlightScorer().score(
        signals,
        BUILTIN_GAME_PROFILES["generic_fps"],
        {asset.id: asset},
    )
    assert len(candidates) == 1
    assert candidates[0].signal_ids == ["kill-1", "reaction-1"]
    assert candidates[0].start_seconds == 28
    assert candidates[0].end_seconds == 51
    assert candidates[0].score > 1


def test_gaming_duration_is_a_maximum_and_shorter_edits_are_valid() -> None:
    asset_id = uuid4()
    candidates = [
        HighlightCandidate(
            asset_id=asset_id,
            start_seconds=0,
            end_seconds=35,
            score=1,
            signal_ids=["highest"],
        ),
        HighlightCandidate(
            asset_id=asset_id,
            start_seconds=40,
            end_seconds=70,
            score=0.9,
            signal_ids=["second"],
        ),
    ]

    limited = GamingHighlightService._select_candidates(candidates, 60, 8)
    assert [candidate.signal_ids for candidate in limited] == [["highest"]]
    assert sum(candidate.duration_seconds for candidate in limited) == 35

    below_limit = GamingHighlightService._select_candidates(candidates, 90, 8)
    assert [candidate.signal_ids for candidate in below_limit] == [
        ["highest"],
        ["second"],
    ]
    assert sum(candidate.duration_seconds for candidate in below_limit) == 65


def test_gaming_profile_api_creates_evidence_linked_plan(
    client: TestClient,
    media_root: Path,
) -> None:
    profiles = client.get("/api/v1/gaming/profiles")
    assert profiles.status_code == 200
    assert {profile["id"] for profile in profiles.json()} == {"generic_fps", "generic_moba"}

    project = client.post("/api/v1/projects", json={"name": "FPS session"}).json()
    source = media_root / "fps-session.mkv"
    source.write_bytes(b"fake gameplay")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    ).json()

    audio_roles = client.put(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/audio-tracks",
        json={
            "assignments": [
                {"stream_index": 1, "role": "game"},
                {"stream_index": 2, "role": "microphone"},
            ]
        },
    )
    assert audio_roles.status_code == 200
    assert [track["role"] for track in audio_roles.json()["audio_tracks"]] == [
        "game",
        "microphone",
    ]

    response = client.post(
        f"/api/v1/projects/{project['id']}/gaming/highlight-plans",
        json={
            "brief": {
                "objective": "Create the strongest FPS moments",
                "target_duration_seconds": 10,
            },
            "game_profile_id": "generic_fps",
            "signals": [
                {
                    "id": "kill-1",
                    "asset_id": asset["id"],
                    "timestamp_seconds": 5,
                    "signal_type": "game_event",
                    "event_name": "kill",
                },
                {
                    "id": "mic-1",
                    "asset_id": asset["id"],
                    "timestamp_seconds": 6,
                    "signal_type": "microphone_reaction",
                    "source_audio_stream_index": 2,
                    "confidence": 0.9,
                },
            ],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["plan"]["brief"]["editing_profile"] == "gameplay_highlights"
    assert payload["plan"]["generated_by"] == "gaming-heuristic:generic_fps"
    assert payload["plan"]["segments"][0]["highlight_signal_ids"] == ["kill-1", "mic-1"]
    assert payload["selected_candidates"][0]["score"] > 1


def test_automatic_league_analysis_persists_evidence_and_creates_plan(
    client: TestClient,
    media_root: Path,
) -> None:
    analyzer = FakeLeagueAnalyzer()
    container = client.app.state.container
    container.signal_analyzer = analyzer
    container.automatic_gaming.analyzer = analyzer
    project = client.post("/api/v1/projects", json={"name": "League auto edit"}).json()
    source = media_root / "league-session.mp4"
    source.write_bytes(b"fake League gameplay")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    ).json()

    response = client.post(
        (f"/api/v1/projects/{project['id']}/assets/{asset['id']}/gaming/auto-highlight-plans"),
        json={
            "brief": {
                "objective": "Find the strongest League plays",
                "target_duration_seconds": 10,
            },
            "max_highlights": 3,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["analysis"]["analyzer"] == analyzer.name
    assert payload["analysis"]["signals"][0]["source"] == "test:league-ocr"
    assert payload["plan"]["generated_by"] == "gaming-heuristic:generic_moba"
    assert payload["plan"]["segments"][0]["highlight_signal_ids"] == ["auto-kill-1"]
    analysis_path = Path(payload["analysis_path"])
    assert analysis_path.is_file()
    assert json.loads(analysis_path.read_text(encoding="utf-8"))["asset_id"] == asset["id"]


def test_automatic_league_analysis_reports_missing_local_tools(
    client: TestClient,
    media_root: Path,
) -> None:
    analyzer = FakeLeagueAnalyzer(available=False)
    container = client.app.state.container
    container.signal_analyzer = analyzer
    container.automatic_gaming.analyzer = analyzer
    project = client.post("/api/v1/projects", json={"name": "No OCR"}).json()
    source = media_root / "league-no-ocr.mp4"
    source.write_bytes(b"fake League gameplay")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    ).json()

    response = client.post(
        (f"/api/v1/projects/{project['id']}/assets/{asset['id']}/gaming/auto-highlight-plans"),
        json={"brief": {"objective": "Find League highlights"}},
    )

    assert response.status_code == 503
    assert response.json()["error_type"] == "AnalyzerUnavailableError"
