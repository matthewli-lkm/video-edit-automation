from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from video_edit_automation.application.gaming import HighlightScorer
from video_edit_automation.domain.gaming import HighlightSignal, HighlightSignalType
from video_edit_automation.domain.models import MediaAsset
from video_edit_automation.infrastructure.game_profiles import BUILTIN_GAME_PROFILES


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
