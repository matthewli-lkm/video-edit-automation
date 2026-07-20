from __future__ import annotations

from video_edit_automation.domain.gaming import GameGenre, GameProfile

BUILTIN_GAME_PROFILES: dict[str, GameProfile] = {
    "generic_fps": GameProfile(
        id="generic_fps",
        display_name="Generic FPS highlights",
        genre=GameGenre.FPS,
        pre_roll_seconds=12,
        post_roll_seconds=8,
        merge_gap_seconds=5,
        minimum_score=0.5,
        signal_weights={
            "manual_marker": 2.5,
            "game_event:clutch": 2.2,
            "game_event:multi_kill": 1.8,
            "game_event:victory": 1.5,
            "game_event:round_win": 1.2,
            "game_event:headshot": 1.0,
            "game_event:kill": 0.9,
            "game_event:assist": 0.45,
            "game_event:death": 0.15,
            "telemetry_event": 0.8,
            "ocr_event": 0.7,
            "microphone_reaction": 0.65,
            "audio_peak": 0.3,
            "motion_peak": 0.25,
        },
    ),
    "generic_moba": GameProfile(
        id="generic_moba",
        display_name="Generic MOBA highlights",
        genre=GameGenre.MOBA,
        pre_roll_seconds=18,
        post_roll_seconds=12,
        merge_gap_seconds=8,
        minimum_score=0.5,
        signal_weights={
            "manual_marker": 2.5,
            "game_event:multi_kill": 1.9,
            "game_event:kill_streak": 1.4,
            "game_event:victory": 1.5,
            "game_event:team_fight": 1.25,
            "game_event:objective": 1.1,
            "game_event:champion_kill": 0.9,
            "game_event:kill": 0.8,
            "game_event:assist": 0.5,
            "game_event:death": 0.1,
            "telemetry_event": 0.85,
            "ocr_event": 0.7,
            "microphone_reaction": 0.6,
            "audio_peak": 0.25,
            "motion_peak": 0.2,
        },
    ),
}


def list_builtin_game_profiles() -> list[GameProfile]:
    return sorted(BUILTIN_GAME_PROFILES.values(), key=lambda profile: profile.id)
