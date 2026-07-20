from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from video_edit_automation.domain.models import (
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


class PlanCreateRequest(ApiModel):
    brief: EditBrief
    draft: EditPlanDraft


class PlanGenerateRequest(ApiModel):
    brief: EditBrief
    transcript: list[TranscriptSegment]


class PlanWithValidation(ApiModel):
    plan: EditPlan
    validation: PlanValidationReport


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
