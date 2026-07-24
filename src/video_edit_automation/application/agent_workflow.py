from __future__ import annotations

from pathlib import Path
from uuid import UUID

from video_edit_automation.application.ports import HighlightReviewer, Repository
from video_edit_automation.application.review import HighlightReviewService
from video_edit_automation.application.services import PlanService, RenderService
from video_edit_automation.domain.agent_workflow import (
    HighlightAgentRound,
    HighlightAgentWorkflow,
    HighlightAgentWorkflowState,
    HighlightCorrectionAction,
    HighlightReviewerEvidence,
    HighlightReviewerOutcome,
    HighlightReviewerVerdict,
    HighlightReviewFrameRequest,
    HighlightReviewSegmentEvidence,
)
from video_edit_automation.domain.errors import (
    DomainError,
    EntityNotFoundError,
    InvalidAgentWorkflowError,
    ReviewerUnavailableError,
)
from video_edit_automation.domain.gaming import HighlightSignal
from video_edit_automation.domain.models import (
    EditPlan,
    EditPlanDraft,
    Job,
    JobStatus,
    RenderPreset,
    RenderProfile,
    TimelineSegment,
    utc_now,
)
from video_edit_automation.domain.review import (
    HighlightReviewAction,
    HighlightReviewDecisionInput,
    HighlightReviewDecisionSource,
    HighlightReviewSession,
)

_TERMINAL_STATES = {
    HighlightAgentWorkflowState.APPROVED,
    HighlightAgentWorkflowState.HUMAN_REVIEW_REQUIRED,
    HighlightAgentWorkflowState.TECHNICAL_FAILURE,
}
_RUNNABLE_STATES = {
    HighlightAgentWorkflowState.QUEUED,
    HighlightAgentWorkflowState.REVISION_REQUIRED,
}
_MAX_FRAME_REQUESTS = 12
_MAX_REVIEW_SEGMENTS = 50
_MAX_REVIEW_SIGNALS = 200


class HighlightAgentWorkflowService:
    """Runs a bounded render/review/revision loop around one evidence review session."""

    def __init__(
        self,
        repository: Repository,
        plans: PlanService,
        renders: RenderService,
        reviews: HighlightReviewService,
        reviewer: HighlightReviewer | None,
    ) -> None:
        self.repository = repository
        self.plans = plans
        self.renders = renders
        self.reviews = reviews
        self.reviewer = reviewer

    def create(
        self,
        project_id: UUID,
        review_session_id: UUID,
        render_preset: RenderPreset | None = None,
        maximum_review_rounds: int = 2,
    ) -> HighlightAgentWorkflow:
        reviewer = self._available_reviewer()
        session = self.reviews.get_session(project_id, review_session_id)
        plan = self.plans.get(project_id, session.plan_id)
        asset = self.repository.get_asset(session.asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {session.asset_id} was not found in project {project_id}"
            )
        if asset.source_fingerprint != session.source_fingerprint:
            raise InvalidAgentWorkflowError(
                "The review session source no longer matches the registered media asset"
            )
        preset = render_preset or RenderPreset(profile=RenderProfile.PREVIEW)
        if preset.profile != RenderProfile.PREVIEW:
            raise InvalidAgentWorkflowError(
                "Agent review workflows may create preview renders only"
            )
        self._require_evidence_links(plan, session)
        workflow = HighlightAgentWorkflow(
            project_id=project_id,
            review_session_id=review_session_id,
            initial_plan_id=plan.id,
            current_plan_id=plan.id,
            reviewer_name=reviewer.name,
            maximum_review_rounds=maximum_review_rounds,
            render_preset=preset,
        )
        self.repository.save_highlight_agent_workflow(workflow)
        return workflow

    def get(self, project_id: UUID, workflow_id: UUID) -> HighlightAgentWorkflow:
        workflow = self.repository.get_highlight_agent_workflow(workflow_id)
        if workflow is None or workflow.project_id != project_id:
            raise EntityNotFoundError(
                f"Highlight agent workflow {workflow_id} was not found in project {project_id}"
            )
        return workflow

    def list(self, project_id: UUID) -> list[HighlightAgentWorkflow]:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        return self.repository.list_highlight_agent_workflows(project_id)

    def run(self, workflow_id: UUID) -> HighlightAgentWorkflow:
        workflow = self.repository.get_highlight_agent_workflow(workflow_id)
        if workflow is None:
            raise EntityNotFoundError(f"Highlight agent workflow {workflow_id} was not found")
        if workflow.state in _TERMINAL_STATES or workflow.state not in _RUNNABLE_STATES:
            return workflow
        reviewer = self._available_reviewer()
        session = self.reviews.get_session(workflow.project_id, workflow.review_session_id)

        while workflow.state in _RUNNABLE_STATES:
            round_number = len(workflow.rounds) + 1
            plan = self.plans.get(workflow.project_id, workflow.current_plan_id)
            job, should_run = self.renders.queue(
                workflow.project_id,
                plan.id,
                workflow.render_preset,
            )
            workflow = workflow.model_copy(
                update={
                    "state": HighlightAgentWorkflowState.RENDERING,
                    "rounds": [
                        *workflow.rounds,
                        HighlightAgentRound(
                            round_number=round_number,
                            plan_id=plan.id,
                            render_job_id=job.id,
                        ),
                    ],
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
            self.repository.save_highlight_agent_workflow(workflow)
            if should_run:
                self.renders.run(job.id)
            job = self.renders.get_job(job.id)
            if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                return self._finish_round(
                    workflow,
                    state=HighlightAgentWorkflowState.TECHNICAL_FAILURE,
                    error="An identical preview render is already running",
                )
            if job.status != JobStatus.SUCCEEDED:
                return self._finish_round(
                    workflow,
                    state=HighlightAgentWorkflowState.TECHNICAL_FAILURE,
                    error=job.error or "Preview render failed technical validation",
                )

            try:
                evidence, preview_path = self._build_evidence(workflow, session, plan, job)
                workflow = workflow.model_copy(
                    update={
                        "state": HighlightAgentWorkflowState.REVIEWING,
                        "updated_at": utc_now(),
                    }
                )
                self.repository.save_highlight_agent_workflow(workflow)
                verdict = reviewer.review(evidence, preview_path)
                self._validate_verdict(verdict, evidence)
            except DomainError as exc:
                return self._finish_round(
                    workflow,
                    state=HighlightAgentWorkflowState.HUMAN_REVIEW_REQUIRED,
                    error=str(exc),
                )

            workflow = self._finish_round(
                workflow,
                state=HighlightAgentWorkflowState.REVIEWING,
                verdict=verdict,
            )
            if verdict.outcome == HighlightReviewerOutcome.APPROVE:
                self._record_approval(workflow, session, round_number)
                return self._set_state(workflow, HighlightAgentWorkflowState.APPROVED)
            if verdict.outcome == HighlightReviewerOutcome.HUMAN_REVIEW:
                return self._set_state(
                    workflow,
                    HighlightAgentWorkflowState.HUMAN_REVIEW_REQUIRED,
                    verdict.summary,
                )

            assert verdict.outcome == HighlightReviewerOutcome.REVISE
            try:
                revised_draft = self._revised_draft(plan, session, evidence, verdict)
                if self._same_timeline(plan, revised_draft):
                    raise InvalidAgentWorkflowError(
                        "Reviewer corrections did not change the current timeline"
                    )
            except DomainError as exc:
                return self._set_state(
                    workflow,
                    HighlightAgentWorkflowState.HUMAN_REVIEW_REQUIRED,
                    str(exc),
                )
            if round_number >= workflow.maximum_review_rounds:
                return self._set_state(
                    workflow,
                    HighlightAgentWorkflowState.HUMAN_REVIEW_REQUIRED,
                    "Reviewer requested another revision after the automatic round limit",
                )

            try:
                revised_plan, _report = self.plans.create_derived(
                    workflow.project_id,
                    plan.brief,
                    revised_draft,
                    generated_by=(
                        f"highlight-reviewer:{workflow.reviewer_name}:round-{round_number}"
                    ),
                )
                self._record_corrections(
                    workflow,
                    session,
                    evidence,
                    verdict,
                    round_number,
                )
            except DomainError as exc:
                return self._set_state(
                    workflow,
                    HighlightAgentWorkflowState.HUMAN_REVIEW_REQUIRED,
                    str(exc),
                )
            workflow = workflow.model_copy(
                update={
                    "current_plan_id": revised_plan.id,
                    "state": HighlightAgentWorkflowState.REVISION_REQUIRED,
                    "updated_at": utc_now(),
                }
            )
            self.repository.save_highlight_agent_workflow(workflow)

        return workflow

    def _available_reviewer(self) -> HighlightReviewer:
        if self.reviewer is None or not self.reviewer.available():
            raise ReviewerUnavailableError(
                "No highlight reviewer is configured; set VEA_REVIEWER_MODEL or inject one"
            )
        return self.reviewer

    def _build_evidence(
        self,
        workflow: HighlightAgentWorkflow,
        session: HighlightReviewSession,
        plan: EditPlan,
        job: Job,
    ) -> tuple[HighlightReviewerEvidence, Path]:
        asset = self.repository.get_asset(session.asset_id)
        if asset is None or asset.project_id != workflow.project_id:
            raise EntityNotFoundError(
                f"Media asset {session.asset_id} was not found in project {workflow.project_id}"
            )
        if job.output_path is None or not job.output_path.is_file():
            raise InvalidAgentWorkflowError("Successful render job has no readable preview output")
        rendered_size = job.output_path.stat().st_size
        if rendered_size <= 0:
            raise InvalidAgentWorkflowError("Successful render job has an empty preview output")

        all_signals_by_id = {signal.id: signal for signal in session.signals}
        output_cursor = 0.0
        segments: list[HighlightReviewSegmentEvidence] = []
        for index, segment in enumerate(plan.segments):
            missing = set(segment.highlight_signal_ids) - set(all_signals_by_id)
            if not segment.highlight_signal_ids or missing:
                raise InvalidAgentWorkflowError(
                    "Every reviewer-visible segment must retain known highlight evidence IDs"
                )
            output_end = output_cursor + segment.duration_seconds
            segments.append(
                HighlightReviewSegmentEvidence(
                    segment_ref=f"segment-{index + 1}",
                    segment_index=index,
                    origin_candidate_id=self._origin_candidate_id(session, segment),
                    asset_id=segment.asset_id,
                    source_in_seconds=segment.source_in_seconds,
                    source_out_seconds=segment.source_out_seconds,
                    output_in_seconds=output_cursor,
                    output_out_seconds=output_end,
                    purpose=segment.purpose,
                    signal_ids=segment.highlight_signal_ids,
                )
            )
            output_cursor = output_end

        selected_signal_ids = {
            signal_id for segment in segments for signal_id in segment.signal_ids
        }
        selected_signals = [
            signal for signal in session.signals if signal.id in selected_signal_ids
        ]
        remaining_signal_capacity = _MAX_REVIEW_SIGNALS - len(selected_signals)
        unselected_signals = sorted(
            (signal for signal in session.signals if signal.id not in selected_signal_ids),
            key=lambda signal: (-signal.confidence, signal.timestamp_seconds, signal.id),
        )[:remaining_signal_capacity]
        supplied_signals = [*selected_signals, *unselected_signals]
        supplied_signal_by_id = {signal.id: signal for signal in supplied_signals}
        evidence = HighlightReviewerEvidence(
            workflow_id=workflow.id,
            review_session_id=session.id,
            review_round=len(workflow.rounds),
            maximum_review_rounds=workflow.maximum_review_rounds,
            project_id=workflow.project_id,
            asset_id=asset.id,
            asset_duration_seconds=asset.duration_seconds,
            current_plan_id=plan.id,
            plan_title=plan.title,
            plan_summary=plan.summary,
            expected_render_duration_seconds=plan.duration_seconds,
            render_job_id=job.id,
            rendered_file_size_bytes=rendered_size,
            segments=segments,
            signals=supplied_signals,
            unselected_signal_ids=[signal.id for signal in unselected_signals],
            frame_requests=self._frame_requests(segments, supplied_signal_by_id),
        )
        return evidence, job.output_path

    @staticmethod
    def _origin_candidate_id(
        session: HighlightReviewSession,
        segment: TimelineSegment,
    ) -> UUID | None:
        segment_signals = set(segment.highlight_signal_ids)
        matches = [
            candidate.id
            for candidate in session.candidates
            if set(candidate.signal_ids) == segment_signals
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _frame_requests(
        segments: list[HighlightReviewSegmentEvidence],
        signal_by_id: dict[str, HighlightSignal],
    ) -> list[HighlightReviewFrameRequest]:
        requested: list[HighlightReviewFrameRequest] = []
        seen_timestamps: set[float] = set()

        def add(timestamp: float, label: str) -> None:
            normalized = round(timestamp, 3)
            if normalized in seen_timestamps:
                return
            seen_timestamps.add(normalized)
            requested.append(
                HighlightReviewFrameRequest(
                    output_timestamp_seconds=normalized,
                    label=label,
                )
            )

        for segment in segments:
            duration = segment.output_out_seconds - segment.output_in_seconds
            inset = min(0.5, duration / 2)
            add(segment.output_in_seconds + inset, f"{segment.segment_ref} opening")
            for signal_id in segment.signal_ids:
                signal = signal_by_id[signal_id]
                timestamp = signal.timestamp_seconds
                if segment.source_in_seconds <= timestamp <= segment.source_out_seconds:
                    output_timestamp = segment.output_in_seconds + (
                        timestamp - segment.source_in_seconds
                    )
                    event_name = signal.event_name or signal_id
                    add(output_timestamp, f"{segment.segment_ref} evidence {event_name}")
            add(segment.output_out_seconds - inset, f"{segment.segment_ref} ending")

        if len(requested) <= _MAX_FRAME_REQUESTS:
            return requested
        last_index = len(requested) - 1
        selected_indexes = {
            round(index * last_index / (_MAX_FRAME_REQUESTS - 1))
            for index in range(_MAX_FRAME_REQUESTS)
        }
        return [request for index, request in enumerate(requested) if index in selected_indexes]

    @staticmethod
    def _validate_verdict(
        verdict: HighlightReviewerVerdict,
        evidence: HighlightReviewerEvidence,
    ) -> None:
        known_segment_refs = {segment.segment_ref for segment in evidence.segments}
        known_signal_ids = {signal.id for signal in evidence.signals}
        corrected_refs: set[str] = set()
        for correction in verdict.corrections:
            if correction.segment_ref is not None:
                if correction.segment_ref not in known_segment_refs:
                    raise InvalidAgentWorkflowError(
                        f"Reviewer referenced unknown segment {correction.segment_ref!r}"
                    )
                if correction.segment_ref in corrected_refs:
                    raise InvalidAgentWorkflowError(
                        f"Reviewer corrected segment {correction.segment_ref!r} more than once"
                    )
                corrected_refs.add(correction.segment_ref)
            if not set(correction.signal_ids).issubset(known_signal_ids):
                raise InvalidAgentWorkflowError(
                    "Reviewer add correction referenced unknown evidence signal IDs"
                )
            if (
                correction.end_seconds is not None
                and correction.end_seconds > evidence.asset_duration_seconds
            ):
                raise InvalidAgentWorkflowError(
                    "Reviewer correction extends beyond the registered source duration"
                )

    @staticmethod
    def _revised_draft(
        plan: EditPlan,
        session: HighlightReviewSession,
        evidence: HighlightReviewerEvidence,
        verdict: HighlightReviewerVerdict,
    ) -> EditPlanDraft:
        segment_by_ref = {
            segment_evidence.segment_ref: plan.segments[segment_evidence.segment_index]
            for segment_evidence in evidence.segments
        }
        corrections_by_ref = {
            correction.segment_ref: correction
            for correction in verdict.corrections
            if correction.segment_ref is not None
        }
        signal_by_id = {signal.id: signal for signal in session.signals}
        revised_segments: list[TimelineSegment] = []
        for segment_ref, segment in segment_by_ref.items():
            correction = corrections_by_ref.get(segment_ref)
            if correction is None:
                revised_segments.append(segment)
                continue
            if correction.action == HighlightCorrectionAction.REMOVE:
                continue
            assert correction.action == HighlightCorrectionAction.ADJUST
            assert correction.start_seconds is not None
            assert correction.end_seconds is not None
            if not any(
                correction.start_seconds
                <= signal_by_id[signal_id].timestamp_seconds
                <= correction.end_seconds
                for signal_id in segment.highlight_signal_ids
            ):
                raise InvalidAgentWorkflowError(
                    "An adjusted highlight must retain at least one of its cited evidence signals"
                )
            revised_segments.append(
                segment.model_copy(
                    update={
                        "source_in_seconds": correction.start_seconds,
                        "source_out_seconds": correction.end_seconds,
                        "purpose": correction.purpose or segment.purpose,
                    }
                )
            )

        for correction in verdict.corrections:
            if correction.action != HighlightCorrectionAction.ADD:
                continue
            assert correction.start_seconds is not None
            assert correction.end_seconds is not None
            assert correction.purpose is not None
            if not any(
                correction.start_seconds
                <= signal_by_id[signal_id].timestamp_seconds
                <= correction.end_seconds
                for signal_id in correction.signal_ids
            ):
                raise InvalidAgentWorkflowError(
                    "An added highlight must contain at least one cited evidence signal"
                )
            revised_segments.append(
                TimelineSegment(
                    asset_id=session.asset_id,
                    source_in_seconds=correction.start_seconds,
                    source_out_seconds=correction.end_seconds,
                    purpose=correction.purpose,
                    highlight_signal_ids=correction.signal_ids,
                )
            )

        if not revised_segments:
            raise InvalidAgentWorkflowError("Reviewer corrections would remove every highlight")
        revised_segments.sort(
            key=lambda item: (str(item.asset_id), item.source_in_seconds, item.source_out_seconds)
        )
        for previous, current in zip(revised_segments, revised_segments[1:], strict=False):
            if previous.asset_id == current.asset_id and current.source_in_seconds < (
                previous.source_out_seconds - 1e-6
            ):
                raise InvalidAgentWorkflowError(
                    "Reviewer corrections would create overlapping highlight clips"
                )
        return EditPlanDraft(
            title=plan.title,
            summary=f"{plan.summary} Revised after structured reviewer feedback.",
            segments=revised_segments,
        )

    @staticmethod
    def _same_timeline(plan: EditPlan, draft: EditPlanDraft) -> bool:
        return [segment.model_dump(mode="json") for segment in plan.segments] == [
            segment.model_dump(mode="json") for segment in draft.segments
        ]

    def _record_corrections(
        self,
        workflow: HighlightAgentWorkflow,
        session: HighlightReviewSession,
        evidence: HighlightReviewerEvidence,
        verdict: HighlightReviewerVerdict,
        round_number: int,
    ) -> None:
        segment_by_ref = {segment.segment_ref: segment for segment in evidence.segments}
        for correction in verdict.corrections:
            decision_input: HighlightReviewDecisionInput | None = None
            segment = (
                segment_by_ref.get(correction.segment_ref)
                if correction.segment_ref is not None
                else None
            )
            if correction.action == HighlightCorrectionAction.ADJUST:
                if segment is not None and segment.origin_candidate_id is not None:
                    decision_input = HighlightReviewDecisionInput(
                        action=HighlightReviewAction.ADJUST,
                        candidate_id=segment.origin_candidate_id,
                        start_seconds=correction.start_seconds,
                        end_seconds=correction.end_seconds,
                        note=correction.reason,
                    )
            elif correction.action == HighlightCorrectionAction.REMOVE:
                if segment is not None and segment.origin_candidate_id is not None:
                    decision_input = HighlightReviewDecisionInput(
                        action=HighlightReviewAction.REJECT,
                        candidate_id=segment.origin_candidate_id,
                        note=correction.reason,
                    )
            else:
                decision_input = HighlightReviewDecisionInput(
                    action=HighlightReviewAction.MISSED_HIGHLIGHT,
                    start_seconds=correction.start_seconds,
                    end_seconds=correction.end_seconds,
                    event_name=correction.purpose,
                    note=correction.reason,
                )
            if decision_input is not None:
                self.reviews.record_decision(
                    workflow.project_id,
                    session.id,
                    decision_input,
                    source=HighlightReviewDecisionSource.REVIEWER_AGENT,
                    reviewer_name=workflow.reviewer_name,
                    workflow_id=workflow.id,
                    review_round=round_number,
                )

    def _record_approval(
        self,
        workflow: HighlightAgentWorkflow,
        session: HighlightReviewSession,
        round_number: int,
    ) -> None:
        snapshot = self.reviews.snapshot(workflow.project_id, session.id)
        decided_candidate_ids = {
            decision.candidate_id
            for decision in snapshot.latest_candidate_decisions
            if decision.candidate_id is not None
        }
        for candidate in session.candidates:
            if candidate.id in decided_candidate_ids:
                continue
            self.reviews.record_decision(
                workflow.project_id,
                session.id,
                HighlightReviewDecisionInput(
                    action=HighlightReviewAction.ACCEPT,
                    candidate_id=candidate.id,
                    note="Approved by the bounded reviewer workflow",
                ),
                source=HighlightReviewDecisionSource.REVIEWER_AGENT,
                reviewer_name=workflow.reviewer_name,
                workflow_id=workflow.id,
                review_round=round_number,
            )

    @staticmethod
    def _require_evidence_links(plan: EditPlan, session: HighlightReviewSession) -> None:
        if len(plan.segments) > _MAX_REVIEW_SEGMENTS:
            raise InvalidAgentWorkflowError(
                f"Agent review supports at most {_MAX_REVIEW_SEGMENTS} clips per workflow"
            )
        known_signal_ids = {signal.id for signal in session.signals}
        selected_signal_ids = {
            signal_id for segment in plan.segments for signal_id in segment.highlight_signal_ids
        }
        if len(selected_signal_ids) > _MAX_REVIEW_SIGNALS:
            raise InvalidAgentWorkflowError(
                f"Agent review supports at most {_MAX_REVIEW_SIGNALS} selected evidence signals"
            )
        for segment in plan.segments:
            if segment.asset_id != session.asset_id:
                raise InvalidAgentWorkflowError(
                    "Agent review currently supports one source asset per gaming review"
                )
            if not segment.highlight_signal_ids:
                raise InvalidAgentWorkflowError(
                    "Every automatically reviewed segment must retain highlight signal IDs"
                )
            if not set(segment.highlight_signal_ids).issubset(known_signal_ids):
                raise InvalidAgentWorkflowError(
                    "The edit plan references evidence absent from its review session"
                )

    def _finish_round(
        self,
        workflow: HighlightAgentWorkflow,
        *,
        state: HighlightAgentWorkflowState,
        verdict: HighlightReviewerVerdict | None = None,
        error: str | None = None,
    ) -> HighlightAgentWorkflow:
        current_round = workflow.rounds[-1].model_copy(
            update={"verdict": verdict, "finished_at": utc_now()}
        )
        updated = workflow.model_copy(
            update={
                "rounds": [*workflow.rounds[:-1], current_round],
                "state": state,
                "error": error,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_highlight_agent_workflow(updated)
        return updated

    def _set_state(
        self,
        workflow: HighlightAgentWorkflow,
        state: HighlightAgentWorkflowState,
        error: str | None = None,
    ) -> HighlightAgentWorkflow:
        updated = workflow.model_copy(
            update={"state": state, "error": error, "updated_at": utc_now()}
        )
        self.repository.save_highlight_agent_workflow(updated)
        return updated
