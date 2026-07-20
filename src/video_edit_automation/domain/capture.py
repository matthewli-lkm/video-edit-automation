from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from video_edit_automation.domain.gaming import (
    DetectedGame,
    GameContext,
    HighlightSignal,
)
from video_edit_automation.domain.models import AudioTrackRoleAssignment, utc_now

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Text = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]


class CaptureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapturePlatform(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    OTHER = "other"


class CaptureRecorder(StrEnum):
    OBS = "obs"
    SCREEN_CAPTURE_KIT = "screen_capture_kit"
    WINDOWS_GRAPHICS_CAPTURE = "windows_graphics_capture"
    EXTERNAL = "external"


class CaptureObservation(CaptureModel):
    process_name: str | None = None
    window_title: str | None = None
    bundle_id: str | None = None
    observed_at_seconds: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def has_identity_evidence(self) -> CaptureObservation:
        if not any(
            value and value.strip()
            for value in (self.process_name, self.window_title, self.bundle_id)
        ):
            raise ValueError("At least one process name, window title, or bundle ID is required")
        return self


class GameCatalogEntry(GameContext):
    default_profile_id: NonBlankText
    process_names: list[NonBlankText] = Field(default_factory=list)
    window_title_fragments: list[NonBlankText] = Field(default_factory=list)
    bundle_ids: list[NonBlankText] = Field(default_factory=list)


class ManualBookmarkInput(CaptureModel):
    id: NonBlankText | None = None
    label: NonBlankText = "manual highlight"
    timestamp_seconds: float | None = Field(default=None, ge=0)
    observed_monotonic_ns: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def has_exactly_one_timestamp(self) -> ManualBookmarkInput:
        supplied = sum(
            value is not None for value in (self.timestamp_seconds, self.observed_monotonic_ns)
        )
        if supplied != 1:
            raise ValueError("Provide exactly one of timestamp_seconds or observed_monotonic_ns")
        return self


class CaptureSession(CaptureModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    asset_id: UUID
    platform: CapturePlatform
    recorder: CaptureRecorder
    clock_origin_monotonic_ns: int | None = Field(default=None, ge=0)
    observations: list[CaptureObservation] = Field(default_factory=list)
    detected_game: DetectedGame | None = None
    game_profile_id: str | None = None
    recording_sha256: Sha256Text | None = None
    audio_track_roles: list[AudioTrackRoleAssignment] = Field(default_factory=list)
    signals: list[HighlightSignal] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CapturePackageManifest(CaptureModel):
    schema_version: Literal[1] = 1
    session_id: UUID
    project_id: UUID
    recording_filename: NonBlankText
    recording_size_bytes: int = Field(gt=0)
    recording_sha256: Sha256Text
    platform: CapturePlatform
    recorder: CaptureRecorder = CaptureRecorder.OBS
    clock_origin_monotonic_ns: int | None = Field(default=None, ge=0)
    observations: list[CaptureObservation] = Field(default_factory=list)
    audio_track_roles: list[AudioTrackRoleAssignment] = Field(default_factory=list)
    game_id_override: str | None = None
    game_profile_id: str | None = None
    bookmarks: list[ManualBookmarkInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def package_values_are_safe(self) -> CapturePackageManifest:
        filename = self.recording_filename
        if Path(filename).is_absolute() or "/" in filename or "\\" in filename:
            raise ValueError("recording_filename must be a plain filename")
        bookmark_ids = [bookmark.id for bookmark in self.bookmarks]
        if any(bookmark_id is None for bookmark_id in bookmark_ids):
            raise ValueError("Every package bookmark requires a stable id")
        if len(set(bookmark_ids)) != len(bookmark_ids):
            raise ValueError("Package bookmark ids must be unique")
        return self


class CaptureInboxItemStatus(StrEnum):
    INGESTED = "ingested"
    ALREADY_INGESTED = "already_ingested"
    FAILED = "failed"


class CaptureInboxItemResult(CaptureModel):
    package_name: str
    status: CaptureInboxItemStatus
    session_id: UUID | None = None
    message: str | None = None


class CaptureInboxScanReport(CaptureModel):
    started_at: datetime
    finished_at: datetime
    roots_checked: int = Field(ge=0)
    pending_packages: int = Field(ge=0)
    unavailable_roots: list[Path] = Field(default_factory=list)
    items: list[CaptureInboxItemResult] = Field(default_factory=list)

    @computed_field
    @property
    def ingested_count(self) -> int:
        return sum(item.status == CaptureInboxItemStatus.INGESTED for item in self.items)

    @computed_field
    @property
    def already_ingested_count(self) -> int:
        return sum(item.status == CaptureInboxItemStatus.ALREADY_INGESTED for item in self.items)

    @computed_field
    @property
    def failed_count(self) -> int:
        return sum(item.status == CaptureInboxItemStatus.FAILED for item in self.items)


class CaptureInboxStatus(CaptureModel):
    configured: bool
    automatic_scan_enabled: bool
    poll_seconds: float = Field(gt=0)
    roots: list[Path]
    last_report: CaptureInboxScanReport | None = None
