from __future__ import annotations

import argparse
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from video_edit_automation.domain.capture import (
    CaptureObservation,
    CapturePackageManifest,
    CapturePlatform,
    CaptureRecorder,
)
from video_edit_automation.domain.errors import InvalidCaptureSessionError
from video_edit_automation.domain.models import AudioTrackRole, AudioTrackRoleAssignment
from video_edit_automation.infrastructure.capture_inbox import (
    COPY_CHUNK_BYTES,
    MANIFEST_FILENAME,
    READY_FILENAME,
)
from video_edit_automation.infrastructure.paths import SUPPORTED_MEDIA_SUFFIXES

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ObsPackageResult:
    session_id: UUID
    package_path: Path
    already_packaged: bool


@dataclass(slots=True)
class _RecordingObservation:
    size_bytes: int
    modified_at_ns: int
    stable_since: float


class ObsPackageWriter:
    """Creates the READY package consumed by the Mac-side capture inbox."""

    def __init__(
        self,
        drop_root: Path,
        project_id: UUID,
        observations: list[CaptureObservation] | None = None,
        audio_track_roles: list[AudioTrackRoleAssignment] | None = None,
        game_id_override: str | None = None,
        game_profile_id: str | None = None,
    ) -> None:
        self.drop_root = drop_root.expanduser().resolve()
        self.drop_root.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id
        self.observations = observations or []
        self.audio_track_roles = audio_track_roles or []
        self.game_id_override = game_id_override
        self.game_profile_id = game_profile_id

    def _session_id(self, source: Path, size_bytes: int, modified_at_ns: int) -> UUID:
        identity = "\0".join(
            (
                str(self.project_id),
                str(source).casefold(),
                str(size_bytes),
                str(modified_at_ns),
            )
        )
        return uuid5(NAMESPACE_URL, identity)

    @staticmethod
    def _copy_with_hash(source: Path, partial: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        copied = 0
        if partial.exists() or partial.is_symlink():
            if not (partial.is_file() or partial.is_symlink()):
                raise InvalidCaptureSessionError("Package staging path is not a file")
            partial.unlink()
        try:
            with source.open("rb") as input_file, partial.open("xb") as output_file:
                while chunk := input_file.read(COPY_CHUNK_BYTES):
                    output_file.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
        except Exception:
            if partial.exists() or partial.is_symlink():
                partial.unlink()
            raise
        return copied, digest.hexdigest()

    def package(self, requested_source: Path) -> ObsPackageResult:
        expanded_source = requested_source.expanduser()
        if expanded_source.is_symlink():
            raise InvalidCaptureSessionError("OBS recording may not be a symbolic link")
        try:
            source = expanded_source.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise InvalidCaptureSessionError("OBS recording does not exist") from exc
        if not source.is_file():
            raise InvalidCaptureSessionError("OBS recording must be a regular file")
        if source.suffix.lower() not in SUPPORTED_MEDIA_SUFFIXES:
            raise InvalidCaptureSessionError("OBS recording has an unsupported media extension")

        before = source.stat()
        if before.st_size <= 0:
            raise InvalidCaptureSessionError("OBS recording is empty")
        session_id = self._session_id(source, before.st_size, before.st_mtime_ns)
        package = (self.drop_root / str(session_id)).resolve()
        if not package.is_relative_to(self.drop_root):
            raise InvalidCaptureSessionError("OBS package escaped the configured drop folder")
        if package.is_symlink():
            raise InvalidCaptureSessionError("OBS package directory may not be a symbolic link")
        package.mkdir(exist_ok=True)

        ready = package / READY_FILENAME
        if ready.is_file() and not ready.is_symlink():
            manifest = CapturePackageManifest.model_validate_json(
                (package / MANIFEST_FILENAME).read_bytes()
            )
            if manifest.session_id != session_id or manifest.project_id != self.project_id:
                raise InvalidCaptureSessionError("Existing READY package has another identity")
            return ObsPackageResult(session_id, package, already_packaged=True)

        recording_filename = f"recording{source.suffix.lower()}"
        destination = package / recording_filename
        partial_recording = package / f"{recording_filename}.partial"
        copied, checksum = self._copy_with_hash(source, partial_recording)
        after = source.stat()
        if (
            copied != before.st_size
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            partial_recording.unlink(missing_ok=True)
            raise InvalidCaptureSessionError("OBS recording changed while it was being packaged")
        os.replace(partial_recording, destination)

        manifest = CapturePackageManifest(
            session_id=session_id,
            project_id=self.project_id,
            recording_filename=recording_filename,
            recording_size_bytes=copied,
            recording_sha256=checksum,
            platform=CapturePlatform.WINDOWS,
            recorder=CaptureRecorder.OBS,
            observations=self.observations,
            audio_track_roles=self.audio_track_roles,
            game_id_override=self.game_id_override,
            game_profile_id=self.game_profile_id,
        )
        manifest_partial = package / f"{MANIFEST_FILENAME}.partial"
        manifest_partial.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.replace(manifest_partial, package / MANIFEST_FILENAME)
        ready_partial = package / f"{READY_FILENAME}.partial"
        ready_partial.write_text("", encoding="utf-8")
        os.replace(ready_partial, ready)
        return ObsPackageResult(session_id, package, already_packaged=False)


class ObsRecordingWatcher:
    def __init__(
        self,
        recordings_root: Path,
        writer: ObsPackageWriter,
        settle_seconds: float = 30,
        poll_seconds: float = 5,
    ) -> None:
        if settle_seconds < 0 or poll_seconds <= 0:
            raise ValueError("Settle time must be non-negative and poll time must be positive")
        self.recordings_root = recordings_root.expanduser().resolve()
        self.writer = writer
        self.settle_seconds = settle_seconds
        self.poll_seconds = poll_seconds
        self._observations: dict[Path, _RecordingObservation] = {}
        self._processed: set[Path] = set()

    def scan_once(self, now: float | None = None) -> list[ObsPackageResult]:
        observed_now = time.monotonic() if now is None else now
        current_paths: set[Path] = set()
        results: list[ObsPackageResult] = []
        for candidate in sorted(self.recordings_root.iterdir(), key=lambda path: path.name):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.suffix.lower() not in SUPPORTED_MEDIA_SUFFIXES:
                continue
            path = candidate.resolve()
            current_paths.add(path)
            if path in self._processed:
                continue
            stat = path.stat()
            previous = self._observations.get(path)
            if (
                previous is None
                or previous.size_bytes != stat.st_size
                or previous.modified_at_ns != stat.st_mtime_ns
            ):
                self._observations[path] = _RecordingObservation(
                    size_bytes=stat.st_size,
                    modified_at_ns=stat.st_mtime_ns,
                    stable_since=observed_now,
                )
                continue
            if observed_now - previous.stable_since < self.settle_seconds:
                continue
            try:
                result = self.writer.package(path)
            except (InvalidCaptureSessionError, OSError):
                logger.exception("Failed to package stable OBS recording %s", path)
                previous.stable_since = observed_now
                continue
            self._processed.add(path)
            results.append(result)

        missing = set(self._observations) - current_paths
        for path in missing:
            self._observations.pop(path, None)
            self._processed.discard(path)
        return results

    def run(self) -> None:
        logger.info("Watching OBS recordings in %s", self.recordings_root)
        while True:
            try:
                for result in self.scan_once():
                    logger.info("OBS package ready: %s", result.package_path)
            except OSError:
                logger.exception("OBS recordings folder is unavailable")
            time.sleep(self.poll_seconds)


def _audio_role(value: str) -> AudioTrackRoleAssignment:
    try:
        stream_text, role_text = value.split("=", maxsplit=1)
        return AudioTrackRoleAssignment(
            stream_index=int(stream_text),
            role=AudioTrackRole(role_text),
        )
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "Audio roles use STREAM_INDEX=ROLE, for example 1=game"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch completed OBS recordings and package them for a Mac capture inbox."
    )
    parser.add_argument("--recordings-root", type=Path, required=True)
    parser.add_argument("--drop-root", type=Path, required=True)
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--game-profile-id", default=None)
    parser.add_argument("--process-name", default=None)
    parser.add_argument("--window-title", default=None)
    parser.add_argument("--audio-role", type=_audio_role, action="append", default=[])
    parser.add_argument("--settle-seconds", type=float, default=30)
    parser.add_argument("--poll-seconds", type=float, default=5)
    return parser


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    observations = []
    if args.process_name or args.window_title:
        observations.append(
            CaptureObservation(
                process_name=args.process_name,
                window_title=args.window_title,
            )
        )
    writer = ObsPackageWriter(
        drop_root=args.drop_root,
        project_id=args.project_id,
        observations=observations,
        audio_track_roles=args.audio_role,
        game_id_override=args.game_id,
        game_profile_id=args.game_profile_id,
    )
    watcher = ObsRecordingWatcher(
        recordings_root=args.recordings_root,
        writer=writer,
        settle_seconds=args.settle_seconds,
        poll_seconds=args.poll_seconds,
    )
    try:
        watcher.run()
    except KeyboardInterrupt:
        logger.info("OBS companion stopped")


if __name__ == "__main__":
    main()
