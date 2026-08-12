from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from video_edit_automation.domain.gaming import HighlightSignalType
from video_edit_automation.domain.models import MediaAsset
from video_edit_automation.infrastructure.league_ocr_analyzer import (
    LeagueOcrSignalAnalyzer,
    OcrEvent,
    parse_league_ocr_text,
)


def _asset() -> MediaAsset:
    return MediaAsset(
        project_id=uuid4(),
        source_path=Path("/tmp/league-vod.mp4"),
        source_fingerprint="league-vod",
        size_bytes=100,
        modified_at_ns=1,
        duration_seconds=700,
        width=1280,
        height=720,
        frame_rate=60,
        has_audio=True,
    )


def test_parses_league_kill_and_objective_announcements() -> None:
    kill = parse_league_ocr_text("Katar has slain Khafre!", 194)
    objective = parse_league_ocr_text("Red team has slain the Chemtech Drake!", 281)

    assert [(event.event_name, event.metadata) for event in kill] == [
        ("champion_kill", {"killer": "Katar", "victim": "Khafre"})
    ]
    assert [(event.event_name, event.metadata) for event in objective] == [
        ("objective", {"team": "red", "objective": "chemtech drake"})
    ]


def test_objective_parser_tolerates_ocr_punctuation_between_team_words() -> None:
    events = parse_league_ocr_text("Blue.team has slain Baron Nashor", 518)

    assert [(event.event_name, event.metadata) for event in events] == [
        ("objective", {"team": "blue", "objective": "baron nashor"})
    ]


def test_parses_multikill_structure_streak_and_result_announcements() -> None:
    events = parse_league_ocr_text(
        "PENTA KILL! Red turret destroyed. UNSTOPPABLE. VICTORY",
        684,
    )

    assert {event.event_name for event in events} == {
        "multi_kill",
        "objective",
        "kill_streak",
        "victory",
    }


def test_candidate_sampling_covers_peak_context_and_endgame_tail() -> None:
    analyzer = LeagueOcrSignalAnalyzer(
        candidate_radius_seconds=2,
        tail_seconds=10,
        tail_interval_seconds=2,
    )

    candidates = analyzer._candidate_seconds(100, [(40, -10.0)])

    assert candidates[:5] == [38, 39, 40, 41, 42]
    assert candidates[-5:] == [90, 92, 94, 96, 98]


def test_events_become_scoring_signals_with_traceable_ocr_evidence() -> None:
    asset = _asset()
    events = [
        OcrEvent(
            timestamp_seconds=120,
            event_name="champion_kill",
            confidence=0.92,
            raw_text="PlayerOne has slain PlayerTwo",
            metadata={"killer": "PlayerOne", "victim": "PlayerTwo"},
        )
    ]

    signals = LeagueOcrSignalAnalyzer._event_signals(asset, events)

    assert [signal.signal_type for signal in signals] == [
        HighlightSignalType.OCR_EVENT,
        HighlightSignalType.GAME_EVENT,
        HighlightSignalType.GAME_EVENT,
    ]
    assert [signal.event_name for signal in signals] == [
        "champion_kill",
        "champion_kill",
        "death",
    ]
    assert all(signal.metadata["raw_text"] == events[0].raw_text for signal in signals)
    assert len({signal.id for signal in signals}) == len(signals)


def test_nearby_kills_create_a_team_fight_signal() -> None:
    asset = _asset()
    events = [
        OcrEvent(100, "champion_kill", 0.9, "A has slain B", {}),
        OcrEvent(118, "champion_kill", 0.9, "C has slain D", {}),
        OcrEvent(180, "champion_kill", 0.9, "E has slain F", {}),
    ]

    signals = LeagueOcrSignalAnalyzer._team_fight_signals(asset, events)

    assert len(signals) == 1
    assert signals[0].event_name == "team_fight"
    assert signals[0].timestamp_seconds == 109
    assert signals[0].metadata["kill_count"] == 2


def test_kill_detection_is_unavailable_without_tesseract() -> None:
    analyzer = LeagueOcrSignalAnalyzer()
    real_which = __import__("shutil").which

    def fake_which(executable: str) -> str | None:
        if executable == analyzer.tesseract_binary:
            return None
        if executable == analyzer.ffmpeg_binary:
            return "/usr/local/bin/ffmpeg"
        return real_which(executable)

    with patch(
        "video_edit_automation.infrastructure.league_ocr_analyzer.shutil.which",
        side_effect=fake_which,
    ):
        assert analyzer.available() is False
