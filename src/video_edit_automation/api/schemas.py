from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from video_edit_automation.domain.gaming import HighlightCandidate, HighlightSignal
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
