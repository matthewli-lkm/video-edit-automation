from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from video_edit_automation.application.ports import Repository
from video_edit_automation.application.review import HighlightReviewService
from video_edit_automation.application.services import PlanService
from video_edit_automation.domain.errors import EntityNotFoundError, InvalidEditPlanError
from video_edit_automation.domain.gaming import (
    GameContext,
    GameGenre,
    HighlightAnalysis,
    HighlightCandidate,
    HighlightSignal,
    HighlightSignalType,
    ManualHighlightClip,
)
from video_edit_automation.domain.models import (
    EditBrief,
    EditingProfile,
    EditPlan,
    EditPlanDraft,
    PlanValidationReport,
    TimelineSegment,
)
from video_edit_automation.domain.review import HighlightReviewSession


@dataclass(frozen=True, slots=True)
class ManualGamingHighlightResult:
    analysis: HighlightAnalysis
    plan: EditPlan
    validation: PlanValidationReport
    selected_candidates: list[HighlightCandidate]
    review_session: HighlightReviewSession


class ManualGamingHighlightService:
    """Creates an evidence-linked review plan from exact human-selected ranges."""

    def __init__(
        self,
        repository: Repository,
        plans: PlanService,
        reviews: HighlightReviewService,
    ) -> None:
        self.repository = repository
        self.plans = plans
        self.reviews = reviews

    def create_plan(
        self,
        project_id: UUID,
        asset_id: UUID,
        brief: EditBrief,
        clips: list[ManualHighlightClip],
    ) -> ManualGamingHighlightResult:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        asset = self.repository.get_asset(asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {asset_id} was not found in project {project_id}"
            )
        if not clips:
            raise InvalidEditPlanError("Add at least one manual clip")

        ordered = sorted(clips, key=lambda clip: (clip.start_seconds, clip.end_seconds))
        for index, clip in enumerate(ordered):
            if clip.end_seconds > asset.duration_seconds:
                raise InvalidEditPlanError(
                    f"Manual clip {index + 1} ends after the source video"
                )
            if index and clip.start_seconds < ordered[index - 1].end_seconds:
                raise InvalidEditPlanError("Manual clips cannot overlap")

        signals = [
            HighlightSignal(
                id=f"manual-marker-{uuid4()}",
                asset_id=asset.id,
                timestamp_seconds=clip.start_seconds,
                duration_seconds=clip.duration_seconds,
                signal_type=HighlightSignalType.MANUAL_MARKER,
                event_name=clip.title,
                confidence=1,
                source="human:manual-workflow",
            )
            for clip in ordered
        ]
        analysis = HighlightAnalysis(
            analyzer="manual-selection",
            asset_id=asset.id,
            source_fingerprint=asset.source_fingerprint,
            game=GameContext(
                game_id="manual",
                display_name="Manual selection",
                genre=GameGenre.OTHER,
            ),
            game_profile_id="manual",
            sampled_frame_count=0,
            audio_peak_count=0,
            signals=signals,
        )
        self.repository.save_highlight_analysis(project_id, analysis)

        candidates = [
            HighlightCandidate(
                asset_id=asset.id,
                start_seconds=clip.start_seconds,
                end_seconds=clip.end_seconds,
                score=1,
                signal_ids=[signal.id],
                labels=[clip.title],
            )
            for clip, signal in zip(ordered, signals, strict=True)
        ]
        manual_brief = brief.model_copy(
            update={"editing_profile": EditingProfile.GAMEPLAY_HIGHLIGHTS}
        )
        draft = EditPlanDraft(
            title="Manual first cut",
            summary=f"Manual workflow kept {len(candidates)} exact clip selection(s).",
            segments=[
                TimelineSegment(
                    asset_id=asset.id,
                    source_in_seconds=candidate.start_seconds,
                    source_out_seconds=candidate.end_seconds,
                    purpose=candidate.labels[0],
                    highlight_signal_ids=candidate.signal_ids,
                )
                for candidate in candidates
            ],
        )
        plan, validation = self.plans.create_derived(
            project_id,
            manual_brief,
            draft,
            generated_by="manual-workflow",
        )
        review_session = self.reviews.create_session(
            project_id=project_id,
            asset_id=asset.id,
            plan=plan,
            analysis=analysis,
            candidates=candidates,
        )
        return ManualGamingHighlightResult(
            analysis=analysis,
            plan=plan,
            validation=validation,
            selected_candidates=candidates,
            review_session=review_session,
        )
