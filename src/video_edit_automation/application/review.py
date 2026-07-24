from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from video_edit_automation.application.ports import Repository
from video_edit_automation.application.services import PlanService
from video_edit_automation.domain.errors import (
    EntityNotFoundError,
    InvalidHighlightReviewError,
)
from video_edit_automation.domain.gaming import HighlightAnalysis, HighlightCandidate
from video_edit_automation.domain.models import EditPlan, EditPlanDraft, utc_now
from video_edit_automation.domain.review import (
    HighlightEvaluationMetrics,
    HighlightHumanReviewState,
    HighlightReviewAction,
    HighlightReviewCandidate,
    HighlightReviewDecision,
    HighlightReviewDecisionInput,
    HighlightReviewDecisionSource,
    HighlightReviewSession,
    HumanPlanApproval,
)


@dataclass(frozen=True, slots=True)
class HighlightReviewSnapshot:
    session: HighlightReviewSession
    decisions: list[HighlightReviewDecision]
    latest_candidate_decisions: list[HighlightReviewDecision]
    metrics: HighlightEvaluationMetrics


class HighlightReviewService:
    """Persists reviewer labels and measures the deterministic highlight baseline."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def create_session(
        self,
        project_id: UUID,
        asset_id: UUID,
        plan: EditPlan,
        analysis: HighlightAnalysis,
        candidates: list[HighlightCandidate],
    ) -> HighlightReviewSession:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        asset = self.repository.get_asset(asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {asset_id} was not found in project {project_id}"
            )
        saved_analysis = self.repository.get_highlight_analysis(analysis.id)
        if saved_analysis is None or saved_analysis.asset_id != asset_id:
            raise EntityNotFoundError(
                f"Highlight analysis {analysis.id} was not found for asset {asset_id}"
            )
        saved_plan = self.repository.get_plan(plan.id)
        if saved_plan is None or saved_plan.project_id != project_id:
            raise EntityNotFoundError(f"Edit plan {plan.id} was not found in project {project_id}")
        if analysis.asset_id != asset_id or analysis.source_fingerprint != asset.source_fingerprint:
            raise InvalidHighlightReviewError(
                "Gaming analysis does not match the source asset being reviewed"
            )
        if analysis.game_profile_id is None:
            raise InvalidHighlightReviewError("Gaming analysis does not record its scoring profile")
        if not candidates or len(candidates) != len(plan.segments):
            raise InvalidHighlightReviewError(
                "A review session requires one selected candidate per edit-plan segment"
            )
        signal_ids = [signal.id for signal in analysis.signals]
        if len(set(signal_ids)) != len(signal_ids):
            raise InvalidHighlightReviewError("Gaming analysis contains duplicate signal IDs")
        known_signal_ids = set(signal_ids)

        review_candidates: list[HighlightReviewCandidate] = []
        for index, (candidate, segment) in enumerate(zip(candidates, plan.segments, strict=True)):
            if candidate.asset_id != asset_id or segment.asset_id != asset_id:
                raise InvalidHighlightReviewError(
                    "Every reviewed candidate and plan segment must reference the source asset"
                )
            if (
                abs(candidate.start_seconds - segment.source_in_seconds) > 1e-6
                or abs(candidate.end_seconds - segment.source_out_seconds) > 1e-6
                or candidate.signal_ids != segment.highlight_signal_ids
            ):
                raise InvalidHighlightReviewError(
                    "Selected candidates no longer match the persisted edit plan"
                )
            if not set(candidate.signal_ids).issubset(known_signal_ids):
                raise InvalidHighlightReviewError(
                    "A selected candidate references evidence missing from the analysis"
                )
            review_candidates.append(
                HighlightReviewCandidate(
                    asset_id=asset_id,
                    plan_segment_index=index,
                    start_seconds=candidate.start_seconds,
                    end_seconds=candidate.end_seconds,
                    score=candidate.score,
                    signal_ids=candidate.signal_ids,
                    labels=candidate.labels,
                )
            )

        session = HighlightReviewSession(
            project_id=project_id,
            asset_id=asset_id,
            analysis_id=analysis.id,
            plan_id=plan.id,
            analyzer=analysis.analyzer,
            game_profile_id=analysis.game_profile_id,
            source_fingerprint=analysis.source_fingerprint,
            candidates=review_candidates,
            signals=analysis.signals,
        )
        self.repository.save_highlight_review_session(session)
        return session

    def list_sessions(self, project_id: UUID) -> list[HighlightReviewSession]:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        return self.repository.list_highlight_review_sessions(project_id)

    def get_session(self, project_id: UUID, session_id: UUID) -> HighlightReviewSession:
        session = self.repository.get_highlight_review_session(session_id)
        if session is None or session.project_id != project_id:
            raise EntityNotFoundError(
                f"Highlight review {session_id} was not found in project {project_id}"
            )
        return session

    def snapshot(self, project_id: UUID, session_id: UUID) -> HighlightReviewSnapshot:
        session = self.get_session(project_id, session_id)
        decisions = self.repository.list_highlight_review_decisions(session_id)
        decisions.sort(key=lambda decision: (decision.created_at, str(decision.id)))
        latest = self._latest_candidate_decisions(session, decisions)
        return HighlightReviewSnapshot(
            session=session,
            decisions=decisions,
            latest_candidate_decisions=latest,
            metrics=self._metrics(session, decisions, latest),
        )

    def record_decision(
        self,
        project_id: UUID,
        session_id: UUID,
        decision_input: HighlightReviewDecisionInput,
        *,
        source: HighlightReviewDecisionSource = HighlightReviewDecisionSource.HUMAN,
        reviewer_name: str | None = None,
        workflow_id: UUID | None = None,
        review_round: int | None = None,
    ) -> HighlightReviewSnapshot:
        session = self.get_session(project_id, session_id)
        asset = self.repository.get_asset(session.asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {session.asset_id} was not found in project {project_id}"
            )
        candidate_ids = {candidate.id for candidate in session.candidates}
        if (
            decision_input.candidate_id is not None
            and decision_input.candidate_id not in candidate_ids
        ):
            raise EntityNotFoundError(
                f"Review candidate {decision_input.candidate_id} was not found in "
                f"highlight review {session_id}"
            )
        if decision_input.start_seconds is not None:
            assert decision_input.end_seconds is not None
            if decision_input.end_seconds > asset.duration_seconds:
                raise InvalidHighlightReviewError(
                    f"Review range ends at {decision_input.end_seconds:.3f}s but asset duration "
                    f"is {asset.duration_seconds:.3f}s"
                )

        existing = self.repository.list_highlight_review_decisions(session_id)
        revision = 1
        if decision_input.candidate_id is not None:
            revision = (
                max(
                    (
                        decision.revision
                        for decision in existing
                        if decision.candidate_id == decision_input.candidate_id
                    ),
                    default=0,
                )
                + 1
            )
        decision = HighlightReviewDecision(
            **decision_input.model_dump(),
            project_id=project_id,
            review_session_id=session_id,
            revision=revision,
            source=source,
            reviewer_name=reviewer_name,
            workflow_id=workflow_id,
            review_round=review_round,
        )
        self.repository.save_highlight_review_decision(decision)
        return self.snapshot(project_id, session_id)

    @staticmethod
    def _latest_candidate_decisions(
        session: HighlightReviewSession,
        decisions: list[HighlightReviewDecision],
    ) -> list[HighlightReviewDecision]:
        latest_by_candidate: dict[UUID, HighlightReviewDecision] = {}
        for decision in decisions:
            if decision.candidate_id is None:
                continue
            previous = latest_by_candidate.get(decision.candidate_id)
            if previous is None or (
                decision.revision,
                decision.created_at,
                str(decision.id),
            ) > (
                previous.revision,
                previous.created_at,
                str(previous.id),
            ):
                latest_by_candidate[decision.candidate_id] = decision
        candidate_order = {
            candidate.id: candidate.plan_segment_index for candidate in session.candidates
        }
        return sorted(
            latest_by_candidate.values(),
            key=lambda decision: candidate_order[decision.candidate_id],
        )

    @staticmethod
    def _metrics(
        session: HighlightReviewSession,
        decisions: list[HighlightReviewDecision],
        latest: list[HighlightReviewDecision],
    ) -> HighlightEvaluationMetrics:
        candidates = {candidate.id: candidate for candidate in session.candidates}
        accepted = [
            decision for decision in latest if decision.action == HighlightReviewAction.ACCEPT
        ]
        adjusted = [
            decision for decision in latest if decision.action == HighlightReviewAction.ADJUST
        ]
        rejected = [
            decision for decision in latest if decision.action == HighlightReviewAction.REJECT
        ]
        missed = [
            decision
            for decision in decisions
            if decision.action == HighlightReviewAction.MISSED_HIGHLIGHT
        ]
        reviewed_count = len(latest)
        pending_count = len(session.candidates) - reviewed_count
        true_positives = len(accepted) + len(adjusted)
        false_positives = len(rejected)
        false_negatives = len(missed)

        precision = None
        if true_positives + false_positives:
            precision = true_positives / (true_positives + false_positives)
        recall = None
        if true_positives + false_negatives:
            recall = true_positives / (true_positives + false_negatives)
        f1_score = None
        if precision is not None and recall is not None and precision + recall:
            f1_score = 2 * precision * recall / (precision + recall)

        accepted_duration = sum(
            candidates[decision.candidate_id].duration_seconds for decision in accepted
        )
        boundary_adjustment = 0.0
        for decision in adjusted:
            assert decision.candidate_id is not None
            assert decision.start_seconds is not None
            assert decision.end_seconds is not None
            candidate = candidates[decision.candidate_id]
            accepted_duration += decision.end_seconds - decision.start_seconds
            boundary_adjustment += abs(decision.start_seconds - candidate.start_seconds)
            boundary_adjustment += abs(decision.end_seconds - candidate.end_seconds)

        mean_boundary_error = None
        if adjusted:
            mean_boundary_error = boundary_adjustment / (2 * len(adjusted))
        correction_count = len(adjusted) + len(rejected) + len(missed)
        correction_denominator = reviewed_count + len(missed)
        missed_event_counts: Counter[str] = Counter()
        for decision in missed:
            assert decision.event_name is not None
            event_name = decision.event_name.strip().lower().replace(" ", "_")
            missed_event_counts[event_name] += 1
        review_complete = pending_count == 0
        return HighlightEvaluationMetrics(
            review_session_id=session.id,
            candidate_count=len(session.candidates),
            reviewed_candidate_count=reviewed_count,
            pending_candidate_count=pending_count,
            accepted_candidate_count=len(accepted),
            adjusted_candidate_count=len(adjusted),
            rejected_candidate_count=len(rejected),
            missed_highlight_count=len(missed),
            review_complete=review_complete,
            provisional=not review_complete,
            precision=round(precision, 4) if precision is not None else None,
            recall=round(recall, 4) if recall is not None else None,
            f1_score=round(f1_score, 4) if f1_score is not None else None,
            accepted_reel_duration_seconds=round(accepted_duration, 4),
            total_boundary_adjustment_seconds=round(boundary_adjustment, 4),
            mean_boundary_error_seconds=(
                round(mean_boundary_error, 4) if mean_boundary_error is not None else None
            ),
            manual_correction_count=correction_count,
            correction_rate=(
                round(correction_count / correction_denominator, 4) if correction_denominator else 0
            ),
            missed_event_counts=dict(sorted(missed_event_counts.items())),
        )


class HighlightHumanReviewService:
    """Owns version-bound human revisions and approval for final rendering."""

    def __init__(self, repository: Repository, plans: PlanService) -> None:
        self.repository = repository
        self.plans = plans

    def get_state(
        self,
        project_id: UUID,
        review_session_id: UUID,
    ) -> HighlightHumanReviewState:
        session = self._session(project_id, review_session_id)
        state = self.repository.get_highlight_human_review_state(review_session_id)
        if state is not None:
            if state.project_id != project_id:
                raise EntityNotFoundError(
                    f"Human review {review_session_id} was not found in project {project_id}"
                )
            return state
        state = HighlightHumanReviewState(
            project_id=project_id,
            review_session_id=review_session_id,
            initial_plan_id=session.plan_id,
            current_plan_id=session.plan_id,
        )
        self.repository.save_highlight_human_review_state(state)
        return state

    def revise(
        self,
        project_id: UUID,
        review_session_id: UUID,
        expected_plan_id: UUID,
        draft: EditPlanDraft,
    ) -> HighlightHumanReviewState:
        session = self._session(project_id, review_session_id)
        state = self.get_state(project_id, review_session_id)
        if state.current_plan_id != expected_plan_id:
            raise InvalidHighlightReviewError(
                "The review plan changed after this page loaded; refresh before saving"
            )
        current_plan = self.plans.get(project_id, expected_plan_id)
        if any(segment.asset_id != session.asset_id for segment in draft.segments):
            raise InvalidHighlightReviewError(
                "A gaming review revision may reference only its reviewed source asset"
            )
        revised, _report = self.plans.create_derived(
            project_id,
            current_plan.brief,
            draft,
            f"human-review:{review_session_id}",
        )
        updated = state.model_copy(
            update={
                "current_plan_id": revised.id,
                "active_approval_id": None,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_highlight_human_review_state(updated)
        return updated

    def approve(
        self,
        project_id: UUID,
        review_session_id: UUID,
        plan_id: UUID,
        plan_version: int,
    ) -> HighlightHumanReviewState:
        state = self.get_state(project_id, review_session_id)
        if state.current_plan_id != plan_id:
            raise InvalidHighlightReviewError(
                "Only the current review plan can receive human approval"
            )
        plan = self.plans.get(project_id, plan_id)
        if plan.version != plan_version:
            raise InvalidHighlightReviewError(
                "The plan version changed after this page loaded; refresh before approving"
            )
        decisions = self.repository.list_highlight_review_decisions(review_session_id)
        session = self._session(project_id, review_session_id)
        latest = HighlightReviewService._latest_candidate_decisions(session, decisions)
        metrics = HighlightReviewService._metrics(session, decisions, latest)
        if not metrics.review_complete:
            raise InvalidHighlightReviewError(
                "Every proposed highlight must be accepted, adjusted, or rejected before approval"
            )
        approval = HumanPlanApproval(plan_id=plan.id, plan_version=plan.version)
        updated = state.model_copy(
            update={
                "approvals": [*state.approvals, approval],
                "active_approval_id": approval.id,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_highlight_human_review_state(updated)
        return updated

    def _session(self, project_id: UUID, session_id: UUID) -> HighlightReviewSession:
        session = self.repository.get_highlight_review_session(session_id)
        if session is None or session.project_id != project_id:
            raise EntityNotFoundError(
                f"Highlight review {session_id} was not found in project {project_id}"
            )
        return session
