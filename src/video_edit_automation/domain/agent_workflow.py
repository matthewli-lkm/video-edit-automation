from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from video_edit_automation.domain.gaming import HighlightSignal
from video_edit_automation.domain.models import RenderPreset, RenderProfile, utc_now

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AgentWorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HighlightReviewerOutcome(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    HUMAN_REVIEW = "human_review"


class HighlightCorrectionAction(StrEnum):
    ADJUST = "adjust"
    REMOVE = "remove"
    ADD = "add"


class HighlightReviewerCorrection(AgentWorkflowModel):
    action: HighlightCorrectionAction
    segment_ref: NonBlankText | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    purpose: NonBlankText | None = None
    signal_ids: list[NonBlankText] = Field(default_factory=list)
    reason: NonBlankText

    @model_validator(mode="after")
    def fields_match_action(self) -> HighlightReviewerCorrection:
        has_range = self.start_seconds is not None or self.end_seconds is not None
        complete_range = self.start_seconds is not None and self.end_seconds is not None
        if complete_range and self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if len(set(self.signal_ids)) != len(self.signal_ids):
            raise ValueError("signal_ids must be unique within a correction")

        if self.action == HighlightCorrectionAction.REMOVE:
            if self.segment_ref is None:
                raise ValueError("segment_ref is required for remove")
            if has_range or self.purpose is not None or self.signal_ids:
                raise ValueError("remove accepts only segment_ref and reason")
        elif self.action == HighlightCorrectionAction.ADJUST:
            if self.segment_ref is None:
                raise ValueError("segment_ref is required for adjust")
            if not complete_range:
                raise ValueError("start_seconds and end_seconds are required for adjust")
            if self.signal_ids:
                raise ValueError("adjust preserves the segment's existing signal IDs")
        elif self.action == HighlightCorrectionAction.ADD:
            if self.segment_ref is not None:
                raise ValueError("segment_ref is not allowed for add")
            if not complete_range:
                raise ValueError("start_seconds and end_seconds are required for add")
            if self.purpose is None:
                raise ValueError("purpose is required for add")
            if not self.signal_ids:
                raise ValueError("add requires at least one supplied evidence signal ID")
        return self


class HighlightReviewerVerdict(AgentWorkflowModel):
    outcome: HighlightReviewerOutcome
    confidence: float = Field(ge=0, le=1)
    summary: NonBlankText
    corrections: list[HighlightReviewerCorrection] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def corrections_match_outcome(self) -> HighlightReviewerVerdict:
        if self.outcome == HighlightReviewerOutcome.REVISE and not self.corrections:
            raise ValueError("revise requires at least one correction")
        if self.outcome != HighlightReviewerOutcome.REVISE and self.corrections:
            raise ValueError("only revise may include corrections")
        return self


class HighlightReviewSegmentEvidence(AgentWorkflowModel):
    segment_ref: NonBlankText
    segment_index: int = Field(ge=0)
    origin_candidate_id: UUID | None = None
    asset_id: UUID
    source_in_seconds: float = Field(ge=0)
    source_out_seconds: float = Field(gt=0)
    output_in_seconds: float = Field(ge=0)
    output_out_seconds: float = Field(gt=0)
    purpose: str | None = None
    signal_ids: list[NonBlankText] = Field(min_length=1)

    @model_validator(mode="after")
    def ranges_are_ordered(self) -> HighlightReviewSegmentEvidence:
        if self.source_out_seconds <= self.source_in_seconds:
            raise ValueError("source_out_seconds must be greater than source_in_seconds")
        if self.output_out_seconds <= self.output_in_seconds:
            raise ValueError("output_out_seconds must be greater than output_in_seconds")
        return self


class HighlightReviewFrameRequest(AgentWorkflowModel):
    output_timestamp_seconds: float = Field(ge=0)
    label: NonBlankText


class HighlightReviewerEvidence(AgentWorkflowModel):
    workflow_id: UUID
    review_session_id: UUID
    review_round: int = Field(ge=1)
    maximum_review_rounds: int = Field(ge=1, le=5)
    project_id: UUID
    asset_id: UUID
    asset_duration_seconds: float = Field(gt=0)
    current_plan_id: UUID
    plan_title: NonBlankText
    plan_summary: NonBlankText
    expected_render_duration_seconds: float = Field(gt=0)
    render_job_id: UUID
    rendered_file_size_bytes: int = Field(gt=0)
    segments: list[HighlightReviewSegmentEvidence] = Field(min_length=1, max_length=50)
    signals: list[HighlightSignal] = Field(default_factory=list, max_length=200)
    unselected_signal_ids: list[NonBlankText] = Field(default_factory=list, max_length=200)
    frame_requests: list[HighlightReviewFrameRequest] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def evidence_is_consistent(self) -> HighlightReviewerEvidence:
        segment_refs = [segment.segment_ref for segment in self.segments]
        if len(set(segment_refs)) != len(segment_refs):
            raise ValueError("segment references must be unique")
        signal_ids = [signal.id for signal in self.signals]
        if len(set(signal_ids)) != len(signal_ids):
            raise ValueError("signal IDs must be unique")
        known_signal_ids = set(signal_ids)
        for segment in self.segments:
            if segment.asset_id != self.asset_id:
                raise ValueError("every segment must reference the review asset")
            if not set(segment.signal_ids).issubset(known_signal_ids):
                raise ValueError("every segment signal ID must exist in supplied evidence")
        if not set(self.unselected_signal_ids).issubset(known_signal_ids):
            raise ValueError("unselected signal IDs must exist in supplied evidence")
        return self


class HighlightAgentWorkflowState(StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    REVIEWING = "reviewing"
    REVISION_REQUIRED = "revision_required"
    APPROVED = "approved"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    TECHNICAL_FAILURE = "technical_failure"


class HighlightAgentRound(AgentWorkflowModel):
    round_number: int = Field(ge=1)
    plan_id: UUID
    render_job_id: UUID
    verdict: HighlightReviewerVerdict | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class HighlightAgentWorkflow(AgentWorkflowModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    review_session_id: UUID
    initial_plan_id: UUID
    current_plan_id: UUID
    reviewer_name: NonBlankText
    maximum_review_rounds: int = Field(default=2, ge=1, le=5)
    render_preset: RenderPreset = Field(default_factory=RenderPreset)
    state: HighlightAgentWorkflowState = HighlightAgentWorkflowState.QUEUED
    rounds: list[HighlightAgentRound] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def workflow_is_consistent(self) -> HighlightAgentWorkflow:
        if self.render_preset.profile != RenderProfile.PREVIEW:
            raise ValueError("agent review workflows may render previews only")
        round_numbers = [item.round_number for item in self.rounds]
        if round_numbers != list(range(1, len(self.rounds) + 1)):
            raise ValueError("workflow rounds must be sequential")
        if len(self.rounds) > self.maximum_review_rounds:
            raise ValueError("workflow exceeds its configured review-round limit")
        return self


class HighlightReviewFrame(AgentWorkflowModel):
    output_timestamp_seconds: float = Field(ge=0)
    label: NonBlankText
    jpeg_base64: NonBlankText
