from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from video_edit_automation.domain.gaming import HighlightSignal
from video_edit_automation.domain.models import utc_now

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HighlightReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    ADJUST = "adjust"
    MISSED_HIGHLIGHT = "missed_highlight"


class HighlightReviewDecisionSource(StrEnum):
    HUMAN = "human"
    REVIEWER_AGENT = "reviewer_agent"


class HighlightReviewCandidate(ReviewModel):
    id: UUID = Field(default_factory=uuid4)
    asset_id: UUID
    plan_segment_index: int = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    score: float = Field(ge=0)
    signal_ids: list[NonBlankText] = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def end_is_after_start(self) -> HighlightReviewCandidate:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if len(set(self.signal_ids)) != len(self.signal_ids):
            raise ValueError("signal_ids must be unique within a review candidate")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class HighlightReviewSession(ReviewModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    asset_id: UUID
    analysis_id: UUID
    plan_id: UUID
    analyzer: NonBlankText
    game_profile_id: NonBlankText
    source_fingerprint: NonBlankText
    candidates: list[HighlightReviewCandidate] = Field(min_length=1)
    signals: list[HighlightSignal] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def evidence_is_consistent(self) -> HighlightReviewSession:
        candidate_ids = [candidate.id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate IDs must be unique within a review session")
        segment_indexes = [candidate.plan_segment_index for candidate in self.candidates]
        if len(set(segment_indexes)) != len(segment_indexes):
            raise ValueError("plan segment indexes must be unique within a review session")
        signal_ids = [signal.id for signal in self.signals]
        if len(set(signal_ids)) != len(signal_ids):
            raise ValueError("signal IDs must be unique within a review session")
        known_signal_ids = set(signal_ids)
        for candidate in self.candidates:
            if candidate.asset_id != self.asset_id:
                raise ValueError("every review candidate must reference the session asset")
            if not set(candidate.signal_ids).issubset(known_signal_ids):
                raise ValueError("every candidate signal ID must exist in the review evidence")
        if any(signal.asset_id != self.asset_id for signal in self.signals):
            raise ValueError("every review signal must reference the session asset")
        return self


class HighlightReviewDecisionInput(ReviewModel):
    action: HighlightReviewAction
    candidate_id: UUID | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    event_name: NonBlankText | None = None
    note: NonBlankText | None = None

    @model_validator(mode="after")
    def fields_match_action(self) -> HighlightReviewDecisionInput:
        has_range = self.start_seconds is not None or self.end_seconds is not None
        complete_range = self.start_seconds is not None and self.end_seconds is not None
        if complete_range and self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")

        if self.action in {HighlightReviewAction.ACCEPT, HighlightReviewAction.REJECT}:
            if self.candidate_id is None:
                raise ValueError(f"candidate_id is required for {self.action.value}")
            if has_range:
                raise ValueError(f"timestamps are not allowed for {self.action.value}")
            if self.event_name is not None:
                raise ValueError(f"event_name is not allowed for {self.action.value}")
        elif self.action == HighlightReviewAction.ADJUST:
            if self.candidate_id is None:
                raise ValueError("candidate_id is required for adjust")
            if not complete_range:
                raise ValueError("start_seconds and end_seconds are required for adjust")
            if self.event_name is not None:
                raise ValueError("event_name is not allowed for adjust")
        elif self.action == HighlightReviewAction.MISSED_HIGHLIGHT:
            if self.candidate_id is not None:
                raise ValueError("candidate_id is not allowed for missed_highlight")
            if not complete_range:
                raise ValueError("start_seconds and end_seconds are required for missed_highlight")
            if self.event_name is None:
                raise ValueError("event_name is required for missed_highlight")
        return self


class HighlightReviewDecision(HighlightReviewDecisionInput):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    review_session_id: UUID
    revision: int = Field(default=1, ge=1)
    source: HighlightReviewDecisionSource = HighlightReviewDecisionSource.HUMAN
    reviewer_name: NonBlankText | None = None
    workflow_id: UUID | None = None
    review_round: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def source_metadata_is_consistent(self) -> HighlightReviewDecision:
        metadata = (self.reviewer_name, self.workflow_id, self.review_round)
        if self.source == HighlightReviewDecisionSource.REVIEWER_AGENT and any(
            value is None for value in metadata
        ):
            raise ValueError("reviewer-agent decisions require reviewer, workflow, and round")
        if self.source == HighlightReviewDecisionSource.HUMAN and any(
            value is not None for value in metadata
        ):
            raise ValueError("human decisions cannot claim reviewer-agent metadata")
        return self


class HighlightEvaluationMetrics(ReviewModel):
    review_session_id: UUID
    candidate_count: int = Field(ge=0)
    reviewed_candidate_count: int = Field(ge=0)
    pending_candidate_count: int = Field(ge=0)
    accepted_candidate_count: int = Field(ge=0)
    adjusted_candidate_count: int = Field(ge=0)
    rejected_candidate_count: int = Field(ge=0)
    missed_highlight_count: int = Field(ge=0)
    review_complete: bool
    provisional: bool
    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    f1_score: float | None = Field(default=None, ge=0, le=1)
    accepted_reel_duration_seconds: float = Field(ge=0)
    total_boundary_adjustment_seconds: float = Field(ge=0)
    mean_boundary_error_seconds: float | None = Field(default=None, ge=0)
    manual_correction_count: int = Field(ge=0)
    correction_rate: float = Field(ge=0, le=1)
    missed_event_counts: dict[str, int] = Field(default_factory=dict)
