from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from video_edit_automation.domain.capture import CaptureObservation
from video_edit_automation.infrastructure.game_catalog import RegistryGameDetector


def _import_recording(client: TestClient, media_root: Path) -> tuple[dict, dict]:
    project = client.post("/api/v1/projects", json={"name": "League session"}).json()
    source = media_root / "league-obs.mkv"
    source.write_bytes(b"fake league recording")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    ).json()
    return project, asset


def test_registry_recognizes_league_from_windows_observation() -> None:
    detected = RegistryGameDetector().detect(
        CaptureObservation(
            process_name=r"C:\Riot Games\League of Legends\Game\League of Legends.exe",
            window_title="League of Legends (TM) Client",
        )
    )
    assert detected is not None
    assert detected.game_id == "league_of_legends"
    assert detected.game_profile_id == "generic_moba"
    assert detected.confidence == 0.98
    assert len(detected.evidence) == 2


def test_unknown_process_is_not_silently_classified() -> None:
    detected = RegistryGameDetector().detect(
        CaptureObservation(process_name="unknown-game.exe", window_title="Something else")
    )
    assert detected is None


def test_obs_capture_session_bookmark_creates_moba_highlight_plan(
    client: TestClient,
    media_root: Path,
) -> None:
    games = client.get("/api/v1/gaming/games")
    assert games.status_code == 200
    assert games.json()[0]["game_id"] == "league_of_legends"

    project, asset = _import_recording(client, media_root)
    project_id = project["id"]
    session_response = client.post(
        f"/api/v1/projects/{project_id}/capture-sessions",
        json={
            "asset_id": asset["id"],
            "platform": "windows",
            "recorder": "obs",
            "clock_origin_monotonic_ns": 1_000_000_000,
            "observations": [
                {
                    "process_name": "League of Legends.exe",
                    "window_title": "League of Legends (TM) Client",
                }
            ],
            "audio_track_roles": [
                {"stream_index": 1, "role": "game"},
                {"stream_index": 2, "role": "microphone"},
            ],
        },
    )
    assert session_response.status_code == 201
    session = session_response.json()
    assert session["platform"] == "windows"
    assert session["recorder"] == "obs"
    assert session["detected_game"]["game_id"] == "league_of_legends"
    assert session["game_profile_id"] == "generic_moba"

    assets = client.get(f"/api/v1/projects/{project_id}/assets").json()
    assert [track["role"] for track in assets[0]["audio_tracks"]] == [
        "game",
        "microphone",
    ]

    bookmark_response = client.post(
        f"/api/v1/projects/{project_id}/capture-sessions/{session['id']}/bookmarks",
        json={
            "id": "manual-baron-fight",
            "label": "baron team fight",
            "observed_monotonic_ns": 6_000_000_000,
        },
    )
    assert bookmark_response.status_code == 201
    bookmark = bookmark_response.json()
    assert bookmark["timestamp_seconds"] == 5
    assert bookmark["signal_type"] == "manual_marker"

    plan_response = client.post(
        f"/api/v1/projects/{project_id}/capture-sessions/{session['id']}/highlight-plans",
        json={
            "brief": {
                "objective": "Keep the strongest League moments",
                "target_duration_seconds": 10,
            },
            "max_highlights": 5,
        },
    )
    assert plan_response.status_code == 201
    payload = plan_response.json()
    assert payload["validation"]["valid"] is True
    assert payload["plan"]["generated_by"] == "gaming-heuristic:generic_moba"
    assert payload["plan"]["segments"][0]["highlight_signal_ids"] == ["manual-baron-fight"]
    assert payload["plan"]["segments"][0]["source_in_seconds"] == 0
    assert payload["plan"]["segments"][0]["source_out_seconds"] == 10

    saved = client.get(f"/api/v1/projects/{project_id}/capture-sessions/{session['id']}").json()
    assert saved["signals"] == [bookmark]


def test_capture_session_rejects_platform_specific_recorder_mismatch(
    client: TestClient,
    media_root: Path,
) -> None:
    project, asset = _import_recording(client, media_root)
    response = client.post(
        f"/api/v1/projects/{project['id']}/capture-sessions",
        json={
            "asset_id": asset["id"],
            "platform": "macos",
            "recorder": "windows_graphics_capture",
        },
    )
    assert response.status_code == 422
    assert "requires Windows" in response.json()["detail"]


def test_monotonic_bookmark_requires_session_clock_origin(
    client: TestClient,
    media_root: Path,
) -> None:
    project, asset = _import_recording(client, media_root)
    project_id = project["id"]
    session = client.post(
        f"/api/v1/projects/{project_id}/capture-sessions",
        json={
            "asset_id": asset["id"],
            "platform": "windows",
            "recorder": "obs",
            "game_id_override": "league_of_legends",
        },
    ).json()
    response = client.post(
        f"/api/v1/projects/{project_id}/capture-sessions/{session['id']}/bookmarks",
        json={"observed_monotonic_ns": 5_000_000_000},
    )
    assert response.status_code == 422
    assert "no monotonic clock origin" in response.json()["detail"]
