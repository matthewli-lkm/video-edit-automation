from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from video_edit_automation.config import Settings
from video_edit_automation.domain.capture import CapturePackageManifest


def _create_project(client: TestClient) -> dict:
    response = client.post("/api/v1/projects", json={"name": "Automated OBS inbox"})
    assert response.status_code == 201
    return response.json()


def _write_package(
    root: Path,
    project_id: str,
    *,
    content: bytes = b"completed obs recording",
    ready: bool = True,
    checksum: str | None = None,
) -> tuple[Path, str]:
    session_id = str(uuid4())
    package = root / session_id
    package.mkdir()
    recording = package / "recording.mkv"
    recording.write_bytes(content)
    manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "project_id": project_id,
        "recording_filename": recording.name,
        "recording_size_bytes": len(content),
        "recording_sha256": checksum or hashlib.sha256(content).hexdigest(),
        "platform": "windows",
        "recorder": "obs",
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
        "bookmarks": [
            {
                "id": "manual-team-fight",
                "label": "team fight",
                "timestamp_seconds": 5,
            }
        ],
    }
    (package / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    if ready:
        (package / "READY").write_text("", encoding="utf-8")
    return package, session_id


def test_ready_obs_package_is_copied_and_ingested_exactly_once(
    client: TestClient,
    capture_inbox_root: Path,
    settings: Settings,
) -> None:
    project = _create_project(client)
    package, session_id = _write_package(capture_inbox_root, project["id"])

    status = client.get("/api/v1/capture-inbox/status")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert status.json()["automatic_scan_enabled"] is False

    first_scan = client.post("/api/v1/capture-inbox/scan")
    assert first_scan.status_code == 200
    first = first_scan.json()
    assert first["ingested_count"] == 1
    assert first["failed_count"] == 0
    assert first["items"][0]["session_id"] == session_id

    session_response = client.get(f"/api/v1/projects/{project['id']}/capture-sessions/{session_id}")
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["detected_game"]["game_id"] == "league_of_legends"
    assert session["game_profile_id"] == "generic_moba"
    assert session["signals"][0]["id"] == "manual-team-fight"

    assets = client.get(f"/api/v1/projects/{project['id']}/assets").json()
    assert len(assets) == 1
    local_recording = Path(assets[0]["source_path"])
    assert local_recording.read_bytes() == (package / "recording.mkv").read_bytes()
    assert local_recording.is_relative_to(settings.data_dir.resolve())
    assert local_recording.name == f"{session_id}.mkv"

    second_scan = client.post("/api/v1/capture-inbox/scan")
    assert second_scan.status_code == 200
    second = second_scan.json()
    assert second["already_ingested_count"] == 1
    assert second["ingested_count"] == 0
    assert len(client.get(f"/api/v1/projects/{project['id']}/assets").json()) == 1


def test_package_without_ready_marker_remains_pending(
    client: TestClient,
    capture_inbox_root: Path,
) -> None:
    project = _create_project(client)
    _write_package(capture_inbox_root, project["id"], ready=False)
    report = client.post("/api/v1/capture-inbox/scan").json()
    assert report["pending_packages"] == 1
    assert report["items"] == []
    assert client.get(f"/api/v1/projects/{project['id']}/capture-sessions").json() == []


def test_checksum_failure_does_not_leave_partial_or_session(
    client: TestClient,
    capture_inbox_root: Path,
    settings: Settings,
) -> None:
    project = _create_project(client)
    _, session_id = _write_package(
        capture_inbox_root,
        project["id"],
        checksum="0" * 64,
    )
    report = client.post("/api/v1/capture-inbox/scan").json()
    assert report["failed_count"] == 1
    assert "checksum" in report["items"][0]["message"].lower()
    assert client.get(f"/api/v1/projects/{project['id']}/capture-sessions").json() == []
    imports = Path(project["workspace_path"]) / "imports"
    assert not (imports / f"{session_id}.mkv").exists()
    assert not (imports / f"{session_id}.mkv.partial").exists()


def test_unmounted_inbox_is_reported_without_failing_service(
    client: TestClient,
    capture_inbox_root: Path,
) -> None:
    capture_inbox_root.rmdir()
    response = client.post("/api/v1/capture-inbox/scan")
    assert response.status_code == 200
    report = response.json()
    assert report["unavailable_roots"] == [str(capture_inbox_root.resolve())]
    assert report["failed_count"] == 0


def test_package_manifest_rejects_recording_path_traversal() -> None:
    with pytest.raises(ValidationError, match="plain filename"):
        CapturePackageManifest(
            session_id=uuid4(),
            project_id=uuid4(),
            recording_filename="../outside.mkv",
            recording_size_bytes=10,
            recording_sha256="0" * 64,
            platform="windows",
        )
