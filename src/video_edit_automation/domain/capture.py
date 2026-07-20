from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from video_edit_automation.domain.gaming import (
    DetectedGame,
    GameContext,
    HighlightSignal,
)
from video_edit_automation.domain.models import AudioTrackRoleAssignment, utc_now

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
    audio_track_roles: list[AudioTrackRoleAssignment] = Field(default_factory=list)
    signals: list[HighlightSignal] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
