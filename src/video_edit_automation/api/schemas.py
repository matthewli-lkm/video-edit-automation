from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from video_edit_automation.domain.capture import (
    CaptureObservation,
    CapturePlatform,
    CaptureRecorder,
)
from video_edit_automation.domain.diagnostics import DesktopDiagnostics
from video_edit_automation.domain.gaming import (
    HighlightAnalysis,
    HighlightCandidate,
    HighlightSignal,
    ManualHighlightClip,
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
from video_edit_automation.domain.review import (
    HighlightEvaluationMetrics,
    HighlightHumanReviewState,
    HighlightReviewDecision,
    HighlightReviewSession,
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
    review_session: HighlightReviewSession


class ManualGamingHighlightPlanRequest(ApiModel):
    brief: EditBrief
    clips: list[ManualHighlightClip] = Field(min_length=1, max_length=100)


class ManualGamingHighlightPlanResponse(GamingHighlightPlanResponse):
    analysis: HighlightAnalysis
    review_session: HighlightReviewSession


class HighlightReviewSnapshotResponse(ApiModel):
    session: HighlightReviewSession
    decisions: list[HighlightReviewDecision]
    latest_candidate_decisions: list[HighlightReviewDecision]
    metrics: HighlightEvaluationMetrics


class HumanPlanRevisionRequest(ApiModel):
    expected_plan_id: UUID
    draft: EditPlanDraft


class HumanPlanApprovalRequest(ApiModel):
    plan_id: UUID
    plan_version: int = Field(ge=1)


class HighlightHumanReviewStateResponse(ApiModel):
    state: HighlightHumanReviewState
    plan: EditPlan
    validation: PlanValidationReport


class HighlightAgentWorkflowCreateRequest(ApiModel):
    render_preset: RenderPreset = Field(default_factory=RenderPreset)
    maximum_review_rounds: int = Field(default=2, ge=1, le=5)


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
    highlight_reviewer: str


class DesktopDiagnosticsResponse(DesktopDiagnostics):
    pass
