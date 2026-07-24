from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from video_edit_automation.domain.capture import CaptureObservation, CapturePackageManifest
from video_edit_automation.domain.models import AudioTrackRole, AudioTrackRoleAssignment
from video_edit_automation.infrastructure.obs_companion import (
    ObsPackageWriter,
    ObsRecordingWatcher,
)


def test_obs_writer_creates_verified_ready_package(tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    drop = tmp_path / "ready"
    recordings.mkdir()
    source = recordings / "League match.mkv"
    source.write_bytes(b"stable obs recording")
    project_id = uuid4()
    writer = ObsPackageWriter(
        drop_root=drop,
        project_id=project_id,
        observations=[CaptureObservation(process_name="League of Legends.exe")],
        audio_track_roles=[AudioTrackRoleAssignment(stream_index=1, role=AudioTrackRole.GAME)],
        game_id_override="league_of_legends",
    )

    result = writer.package(source)
    assert result.already_packaged is False
    assert (result.package_path / "READY").is_file()
    manifest = CapturePackageManifest.model_validate_json(
        (result.package_path / "session.json").read_bytes()
    )
    packaged_recording = result.package_path / manifest.recording_filename
    assert packaged_recording.read_bytes() == source.read_bytes()
    assert manifest.project_id == project_id
    assert manifest.recording_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest.audio_track_roles[0].role == AudioTrackRole.GAME

    repeated = writer.package(source)
    assert repeated.session_id == result.session_id
    assert repeated.already_packaged is True


def test_obs_watcher_waits_for_file_to_remain_stable(tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    source = recordings / "match.mkv"
    source.write_bytes(b"part one")
    writer = ObsPackageWriter(tmp_path / "ready", uuid4())
    watcher = ObsRecordingWatcher(recordings, writer, settle_seconds=5, poll_seconds=1)

    assert watcher.scan_once(now=0) == []
    source.write_bytes(b"part one plus more")
    assert watcher.scan_once(now=3) == []
    assert watcher.scan_once(now=7.9) == []
    results = watcher.scan_once(now=8)
    assert len(results) == 1
    assert (results[0].package_path / "READY").is_file()


def test_windows_companion_package_is_ingested_by_mac_inbox(
    client: TestClient,
    capture_inbox_root: Path,
    media_root: Path,
) -> None:
    project = client.post("/api/v1/projects", json={"name": "Cross-device League"}).json()
    source = media_root / "windows-obs.mkv"
    source.write_bytes(b"cross-device obs recording")
    writer = ObsPackageWriter(
        capture_inbox_root,
        UUID(project["id"]),
        observations=[
            CaptureObservation(
                process_name="League of Legends.exe",
                window_title="League of Legends (TM) Client",
            )
        ],
        game_id_override="league_of_legends",
    )
    packaged = writer.package(source)

    scan = client.post("/api/v1/capture-inbox/scan")
    assert scan.status_code == 200
    assert scan.json()["ingested_count"] == 1
    session = client.get(
        f"/api/v1/projects/{project['id']}/capture-sessions/{packaged.session_id}"
    ).json()
    assert session["platform"] == "windows"
    assert session["recorder"] == "obs"
    assert session["detected_game"]["game_id"] == "league_of_legends"
