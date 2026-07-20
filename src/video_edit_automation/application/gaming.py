from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from video_edit_automation.application.ports import Repository
from video_edit_automation.application.services import PlanService
from video_edit_automation.domain.errors import EntityNotFoundError, InvalidEditPlanError
from video_edit_automation.domain.gaming import (
    GameProfile,
    HighlightCandidate,
    HighlightSignal,
)
from video_edit_automation.domain.models import (
    EditBrief,
    EditingProfile,
    EditPlan,
    EditPlanDraft,
    MediaAsset,
    PlanValidationReport,
    TimelineSegment,
)


@dataclass(slots=True)
class _CandidateWindow:
    asset_id: UUID
    start_seconds: float
    end_seconds: float
    score: float
    signal_ids: list[str]
    labels: list[str]


class HighlightScorer:
    """Deterministic baseline that turns heterogeneous event evidence into clip windows."""

    @staticmethod
    def _weight(signal: HighlightSignal, profile: GameProfile) -> float:
        for key in signal.weight_keys():
            if key in profile.signal_weights:
                return profile.signal_weights[key]
        return 0

    def score(
        self,
        signals: list[HighlightSignal],
        profile: GameProfile,
        assets: dict[UUID, MediaAsset],
    ) -> list[HighlightCandidate]:
        windows: list[_CandidateWindow] = []
        for signal in signals:
            asset = assets.get(signal.asset_id)
            if asset is None:
                raise InvalidEditPlanError(
                    f"Highlight signal {signal.id} references an unknown project asset"
                )
            if signal.timestamp_seconds > asset.duration_seconds:
                raise InvalidEditPlanError(
                    f"Highlight signal {signal.id} occurs after the source video ends"
                )
            weight = self._weight(signal, profile)
            score = weight * signal.confidence
            if score <= 0:
                continue
            label = signal.normalized_event_name or signal.signal_type.value
            windows.append(
                _CandidateWindow(
                    asset_id=asset.id,
                    start_seconds=max(0, signal.timestamp_seconds - profile.pre_roll_seconds),
                    end_seconds=min(
                        asset.duration_seconds,
                        signal.timestamp_seconds
                        + signal.duration_seconds
                        + profile.post_roll_seconds,
                    ),
                    score=score,
                    signal_ids=[signal.id],
                    labels=[label],
                )
            )

        windows.sort(key=lambda item: (str(item.asset_id), item.start_seconds))
        merged: list[_CandidateWindow] = []
        for window in windows:
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and previous.asset_id == window.asset_id
                and window.start_seconds <= previous.end_seconds + profile.merge_gap_seconds
            ):
                previous.end_seconds = max(previous.end_seconds, window.end_seconds)
                previous.score += window.score
                previous.signal_ids.extend(window.signal_ids)
                previous.labels = list(dict.fromkeys([*previous.labels, *window.labels]))
            else:
                merged.append(window)

        return [
            HighlightCandidate(
                asset_id=window.asset_id,
                start_seconds=window.start_seconds,
                end_seconds=window.end_seconds,
                score=round(window.score, 4),
                signal_ids=window.signal_ids,
                labels=window.labels,
            )
            for window in merged
            if window.score >= profile.minimum_score and window.end_seconds > window.start_seconds
        ]


class GamingHighlightService:
    def __init__(
        self,
        repository: Repository,
        plans: PlanService,
        profiles: dict[str, GameProfile],
        scorer: HighlightScorer | None = None,
    ) -> None:
        self.repository = repository
        self.plans = plans
        self.profiles = profiles
        self.scorer = scorer or HighlightScorer()

    def list_profiles(self) -> list[GameProfile]:
        return sorted(self.profiles.values(), key=lambda profile: profile.id)

    def _profile(self, profile_id: str) -> GameProfile:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise EntityNotFoundError(f"Gaming profile {profile_id!r} was not found")
        return profile

    @staticmethod
    def _select_candidates(
        candidates: list[HighlightCandidate],
        target_duration_seconds: float | None,
        max_highlights: int,
    ) -> list[HighlightCandidate]:
        ranked = sorted(candidates, key=lambda item: (-item.score, item.duration_seconds))
        selected: list[HighlightCandidate] = []
        selected_duration = 0.0
        budget = target_duration_seconds * 1.10 if target_duration_seconds else None
        for candidate in ranked:
            if len(selected) >= max_highlights:
                break
            fits_budget = budget is None or selected_duration + candidate.duration_seconds <= budget
            if fits_budget or not selected:
                selected.append(candidate)
                selected_duration += candidate.duration_seconds
        return sorted(selected, key=lambda item: (str(item.asset_id), item.start_seconds))

    def create_plan(
        self,
        project_id: UUID,
        brief: EditBrief,
        profile_id: str,
        signals: list[HighlightSignal],
        max_highlights: int = 20,
    ) -> tuple[EditPlan, PlanValidationReport, list[HighlightCandidate]]:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        profile = self._profile(profile_id)
        assets = {asset.id: asset for asset in self.repository.list_assets(project_id)}
        candidates = self.scorer.score(signals, profile, assets)
        selected = self._select_candidates(
            candidates,
            brief.target_duration_seconds,
            max_highlights,
        )
        if not selected:
            raise InvalidEditPlanError(
                "No highlight candidates reached this gaming profile's minimum score"
            )

        gaming_brief = brief.model_copy(
            update={"editing_profile": EditingProfile.GAMEPLAY_HIGHLIGHTS}
        )
        draft = EditPlanDraft(
            title=f"{profile.display_name} edit",
            summary=(
                f"Deterministic baseline selected {len(selected)} highlight window(s) from "
                f"{len(signals)} supplied signal(s)."
            ),
            segments=[
                TimelineSegment(
                    asset_id=candidate.asset_id,
                    source_in_seconds=candidate.start_seconds,
                    source_out_seconds=candidate.end_seconds,
                    purpose=", ".join(candidate.labels),
                    highlight_signal_ids=candidate.signal_ids,
                )
                for candidate in selected
            ],
        )
        plan, report = self.plans.create_derived(
            project_id,
            gaming_brief,
            draft,
            generated_by=f"gaming-heuristic:{profile.id}",
        )
        return plan, report, selected
