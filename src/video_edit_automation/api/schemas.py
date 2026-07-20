from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from video_edit_automation.domain.capture import (
    CaptureObservation,
    CapturePlatform,
    CaptureRecorder,
)
from video_edit_automation.domain.gaming import (
    HighlightAnalysis,
    HighlightCandidate,
    HighlightSignal,
)
from video_edit_automation.domain.models import (
    AudioTrackRoleAssignment,
    EditBrief,
    EditPlan,
    EditPlanDraft,
    PlanValidationReport,
    RenderPreset,
    TranscriptSegment,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreateRequest(ApiModel):
    name: str


class AssetImportRequest(ApiModel):
    local_path: Path


class AudioTrackRolesRequest(ApiModel):
    assignments: list[AudioTrackRoleAssignment] = Field(min_length=1)


class GameDetectionRequest(ApiModel):
    observation: CaptureObservation


class CaptureSessionCreateRequest(ApiModel):
    asset_id: UUID
    platform: CapturePlatform
    recorder: CaptureRecorder
    clock_origin_monotonic_ns: int | None = Field(default=None, ge=0)
    observations: list[CaptureObservation] = Field(default_factory=list)
    audio_track_roles: list[AudioTrackRoleAssignment] = Field(default_factory=list)
    game_id_override: str | None = None
    game_profile_id: str | None = None


class CaptureSessionHighlightPlanRequest(ApiModel):
    brief: EditBrief
    max_highlights: int = Field(default=20, ge=1, le=100)


class PlanCreateRequest(ApiModel):
    brief: EditBrief
    draft: EditPlanDraft


class PlanGenerateRequest(ApiModel):
    brief: EditBrief
    transcript: list[TranscriptSegment]


class PlanWithValidation(ApiModel):
    plan: EditPlan
    validation: PlanValidationReport


class GamingHighlightPlanRequest(ApiModel):
    brief: EditBrief
    game_profile_id: str
    signals: list[HighlightSignal] = Field(min_length=1)
    max_highlights: int = Field(default=20, ge=1, le=100)


class GamingHighlightPlanResponse(PlanWithValidation):
    selected_candidates: list[HighlightCandidate]


class AutomaticGamingHighlightPlanRequest(ApiModel):
    brief: EditBrief
    game_id: str = "league_of_legends"
    game_profile_id: str | None = None
    max_highlights: int = Field(default=20, ge=1, le=100)


class AutomaticGamingHighlightPlanResponse(GamingHighlightPlanResponse):
    analysis: HighlightAnalysis
    analysis_path: Path


class RenderRequest(ApiModel):
    preset: RenderPreset


class RenderCommandResponse(ApiModel):
    argv: list[str]
    executable: str
    warning: str = "Diagnostic only; do not paste or modify this command for production use."


class HealthResponse(ApiModel):
    status: str
    database: str
    media_tools: str
    local_planner: str
    gaming_analyzer: str
