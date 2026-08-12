from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AspectRatio(StrEnum):
    SOURCE = "source"
    LANDSCAPE = "16:9"
    VERTICAL = "9:16"
    SQUARE = "1:1"


class RenderProfile(StrEnum):
    PREVIEW = "preview"
    FINAL = "final"


class EditingProfile(StrEnum):
    SPOKEN_CONTENT = "spoken_content"
    GAMING_COMMENTARY = "gaming_commentary"
    GAMEPLAY_HIGHLIGHTS = "gameplay_highlights"
    GAMING_MONTAGE = "gaming_montage"
    SHORT_FORM_GAMING = "short_form_gaming"


class CaptureMode(StrEnum):
    HIGHLIGHTS = "highlights"
    FULL_MATCH = "full_match"
    FULL_SESSION = "full_session"
    MANUAL = "manual"


class AudioTrackRole(StrEnum):
    MIXED = "mixed"
    GAME = "game"
    MICROPHONE = "microphone"
    VOICE_CHAT = "voice_chat"
    MUSIC = "music"
    UNKNOWN = "unknown"


class AudioOutputMode(StrEnum):
    SOURCE_MIX = "source_mix"
    GAME_ONLY = "game_only"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PlaybackMode(StrEnum):
    ORIGINAL = "original"
    PROXY = "proxy"


class PlaybackStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class Project(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    name: NonBlankText
    workspace_path: Path | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AudioTrack(StrictModel):
    stream_index: int = Field(ge=0)
    role: AudioTrackRole = AudioTrackRole.UNKNOWN
    codec: str | None = None
    channels: int | None = Field(default=None, gt=0)
    title: str | None = None
    language: str | None = None


class AudioTrackRoleAssignment(StrictModel):
    stream_index: int = Field(ge=0)
    role: AudioTrackRole


class ProbedMedia(StrictModel):
    duration_seconds: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate: float = Field(gt=0)
    has_video: bool = True
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_tracks: list[AudioTrack] = Field(default_factory=list)


class MediaAsset(ProbedMedia):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_path: Path
    source_fingerprint: str
    size_bytes: int = Field(ge=0)
    modified_at_ns: int = Field(ge=0)
    imported_at: datetime = Field(default_factory=utc_now)


class TranscriptSegment(StrictModel):
    id: str | None = None
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: NonBlankText
    confidence: float | None = Field(default=None, ge=0, le=1)
    speaker: str | None = None

    @model_validator(mode="after")
    def end_is_after_start(self) -> TranscriptSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class EditBrief(StrictModel):
    objective: NonBlankText
    editing_profile: EditingProfile = EditingProfile.SPOKEN_CONTENT
    audience: str | None = None
    target_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    aspect_ratio: AspectRatio = AspectRatio.SOURCE
    language: str | None = None
    style: str | None = None
    additional_instructions: str | None = None


class TimelineSegment(StrictModel):
    asset_id: UUID
    source_in_seconds: float = Field(ge=0)
    source_out_seconds: float = Field(gt=0)
    purpose: str | None = None
    transcript_segment_ids: list[str] = Field(default_factory=list)
    highlight_signal_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def out_is_after_in(self) -> TimelineSegment:
        if self.source_out_seconds <= self.source_in_seconds:
            raise ValueError("source_out_seconds must be greater than source_in_seconds")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.source_out_seconds - self.source_in_seconds


class EditPlanDraft(StrictModel):
    title: NonBlankText
    summary: NonBlankText
    segments: list[TimelineSegment] = Field(min_length=1, max_length=500)

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)


class EditPlan(EditPlanDraft):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    version: int = Field(default=1, ge=1)
    brief: EditBrief
    generated_by: str = "manual"
    created_at: datetime = Field(default_factory=utc_now)


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(StrictModel):
    severity: ValidationSeverity
    code: str
    message: str
    segment_index: int | None = None


class PlanValidationReport(StrictModel):
    valid: bool
    total_duration_seconds: float = Field(ge=0)
    issues: list[ValidationIssue] = Field(default_factory=list)


class RenderPreset(StrictModel):
    profile: RenderProfile = RenderProfile.PREVIEW
    aspect_ratio: AspectRatio = AspectRatio.SOURCE
    frames_per_second: int = Field(default=60, ge=1, le=120)
    transition_duration_seconds: float = Field(default=0.25, ge=0, le=2)
    audio_output_mode: AudioOutputMode = AudioOutputMode.SOURCE_MIX


class Job(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    plan_id: UUID
    status: JobStatus = JobStatus.QUEUED
    preset: RenderPreset
    progress: float = Field(default=0, ge=0, le=1)
    output_path: Path | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MediaProxy(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    asset_id: UUID
    source_fingerprint: NonBlankText
    maximum_width: int = Field(default=1280, ge=320, le=3840)
    status: JobStatus = JobStatus.QUEUED
    progress: float = Field(default=0, ge=0, le=1)
    output_path: Path
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AssetPlayback(StrictModel):
    asset_id: UUID
    mode: PlaybackMode
    status: PlaybackStatus
    proxy_id: UUID | None = None
    progress: float = Field(default=0, ge=0, le=1)
    error: str | None = None
    updated_at: datetime | None = None
