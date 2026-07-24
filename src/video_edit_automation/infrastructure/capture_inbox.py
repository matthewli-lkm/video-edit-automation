from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from pathlib import Path

from pydantic import ValidationError

from video_edit_automation.application.capture import CaptureSessionService
from video_edit_automation.application.services import ProjectService
from video_edit_automation.domain.capture import (
    CaptureInboxItemResult,
    CaptureInboxItemStatus,
    CaptureInboxScanReport,
    CaptureInboxStatus,
    CapturePackageManifest,
)
from video_edit_automation.domain.errors import DomainError, InvalidCaptureSessionError
from video_edit_automation.domain.models import utc_now
from video_edit_automation.infrastructure.paths import WorkspaceManager

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "session.json"
READY_FILENAME = "READY"
MAX_MANIFEST_BYTES = 1_000_000
COPY_CHUNK_BYTES = 8 * 1024 * 1024


class CaptureInboxScanner:
    """Safely ingests completed capture packages from local or mounted SMB folders."""

    def __init__(
        self,
        roots: tuple[Path, ...],
        projects: ProjectService,
        captures: CaptureSessionService,
        workspace: WorkspaceManager,
        poll_seconds: float = 15,
        automatic_scan_enabled: bool = True,
    ) -> None:
        self.roots = tuple(dict.fromkeys(root.expanduser().resolve() for root in roots))
        self.projects = projects
        self.captures = captures
        self.workspace = workspace
        self.poll_seconds = poll_seconds
        self.automatic_scan_enabled = automatic_scan_enabled
        self._last_report: CaptureInboxScanReport | None = None
        self._scan_lock = threading.Lock()

    def status(self) -> CaptureInboxStatus:
        return CaptureInboxStatus(
            configured=bool(self.roots),
            automatic_scan_enabled=self.automatic_scan_enabled and bool(self.roots),
            poll_seconds=self.poll_seconds,
            roots=list(self.roots),
            last_report=self._last_report,
        )

    @staticmethod
    def _hash_file(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            while chunk := source.read(COPY_CHUNK_BYTES):
                total += len(chunk)
                digest.update(chunk)
        return total, digest.hexdigest()

    @classmethod
    def _copy_verified(
        cls,
        source: Path,
        destination: Path,
        expected_size: int,
        expected_sha256: str,
    ) -> Path:
        if destination.exists():
            actual_size, actual_sha256 = cls._hash_file(destination)
            if actual_size == expected_size and actual_sha256 == expected_sha256:
                return destination
            raise InvalidCaptureSessionError(
                "Managed recording already exists with different content"
            )

        partial = destination.with_name(f"{destination.name}.partial")
        if partial.exists() or partial.is_symlink():
            if not (partial.is_file() or partial.is_symlink()):
                raise InvalidCaptureSessionError("Managed import staging path is not a file")
            partial.unlink()

        digest = hashlib.sha256()
        copied = 0
        try:
            with source.open("rb") as input_file, partial.open("xb") as output_file:
                while chunk := input_file.read(COPY_CHUNK_BYTES):
                    output_file.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
            if copied != expected_size or digest.hexdigest() != expected_sha256:
                raise InvalidCaptureSessionError(
                    "Recording changed or failed checksum verification during copy"
                )
            os.replace(partial, destination)
        except Exception:
            if partial.exists() or partial.is_symlink():
                partial.unlink()
            raise
        return destination

    @staticmethod
    def _load_manifest(package: Path) -> CapturePackageManifest:
        ready = package / READY_FILENAME
        manifest_path = package / MANIFEST_FILENAME
        if ready.is_symlink() or not ready.is_file():
            raise InvalidCaptureSessionError("READY marker must be a regular file")
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise InvalidCaptureSessionError("Completed package has no regular session.json")
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise InvalidCaptureSessionError("Capture package manifest is too large")
        return CapturePackageManifest.model_validate_json(manifest_path.read_bytes())

    @staticmethod
    def _source_recording(package: Path, manifest: CapturePackageManifest) -> Path:
        requested = package / manifest.recording_filename
        if requested.is_symlink():
            raise InvalidCaptureSessionError("Recording may not be a symbolic link")
        try:
            package_root = package.resolve(strict=True)
            source = requested.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise InvalidCaptureSessionError("Capture package recording is unavailable") from exc
        if not source.is_file() or not source.is_relative_to(package_root):
            raise InvalidCaptureSessionError("Recording escaped its capture package")
        if source.stat().st_size != manifest.recording_size_bytes:
            raise InvalidCaptureSessionError(
                "Recording size does not match the completed package manifest"
            )
        return source

    def _reconcile_bookmarks(
        self,
        manifest: CapturePackageManifest,
    ) -> None:
        session = self.captures.find(manifest.session_id)
        if session is None:
            raise InvalidCaptureSessionError("Capture session disappeared during ingestion")
        known_ids = {signal.id for signal in session.signals}
        for bookmark in manifest.bookmarks:
            if bookmark.id not in known_ids:
                self.captures.add_bookmark(
                    manifest.project_id,
                    manifest.session_id,
                    bookmark,
                )
                if bookmark.id is not None:
                    known_ids.add(bookmark.id)

    def _ingest_package(self, package: Path) -> CaptureInboxItemResult:
        manifest = self._load_manifest(package)
        existing = self.captures.find(manifest.session_id)
        if existing is not None:
            if existing.project_id != manifest.project_id:
                raise InvalidCaptureSessionError(
                    "Package session ID is already used by another project"
                )
            if existing.recording_sha256 != manifest.recording_sha256:
                raise InvalidCaptureSessionError(
                    "Package session ID is already used by another recording"
                )
            self._reconcile_bookmarks(manifest)
            return CaptureInboxItemResult(
                package_name=package.name,
                status=CaptureInboxItemStatus.ALREADY_INGESTED,
                session_id=existing.id,
            )

        self.projects.get(manifest.project_id)
        source = self._source_recording(package, manifest)
        destination = self.workspace.managed_import_path(
            manifest.project_id,
            manifest.session_id,
            source.suffix,
        )
        local_recording = self._copy_verified(
            source,
            destination,
            manifest.recording_size_bytes,
            manifest.recording_sha256,
        )
        asset = self.projects.import_managed_asset(manifest.project_id, local_recording)
        session = self.captures.create(
            project_id=manifest.project_id,
            asset_id=asset.id,
            platform=manifest.platform,
            recorder=manifest.recorder,
            clock_origin_monotonic_ns=manifest.clock_origin_monotonic_ns,
            observations=manifest.observations,
            audio_track_roles=manifest.audio_track_roles,
            game_id_override=manifest.game_id_override,
            game_profile_id=manifest.game_profile_id,
            session_id=manifest.session_id,
            recording_sha256=manifest.recording_sha256,
        )
        self._reconcile_bookmarks(manifest)
        return CaptureInboxItemResult(
            package_name=package.name,
            status=CaptureInboxItemStatus.INGESTED,
            session_id=session.id,
        )

    def scan(self) -> CaptureInboxScanReport:
        with self._scan_lock:
            started_at = utc_now()
            items: list[CaptureInboxItemResult] = []
            pending_packages = 0
            unavailable_roots: list[Path] = []

            for root in self.roots:
                try:
                    packages = sorted(root.iterdir(), key=lambda path: path.name)
                except OSError:
                    unavailable_roots.append(root)
                    continue
                for package in packages:
                    try:
                        if package.is_symlink() or not package.is_dir():
                            continue
                        if not (package / READY_FILENAME).exists():
                            pending_packages += 1
                            continue
                        items.append(self._ingest_package(package))
                    except (DomainError, OSError, ValidationError, ValueError) as exc:
                        items.append(
                            CaptureInboxItemResult(
                                package_name=package.name,
                                status=CaptureInboxItemStatus.FAILED,
                                message=str(exc)[:1_000],
                            )
                        )

            report = CaptureInboxScanReport(
                started_at=started_at,
                finished_at=utc_now(),
                roots_checked=len(self.roots),
                pending_packages=pending_packages,
                unavailable_roots=unavailable_roots,
                items=items,
            )
            self._last_report = report
            return report

    async def monitor(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.scan)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected capture inbox scan failure")
            await asyncio.sleep(self.poll_seconds)
