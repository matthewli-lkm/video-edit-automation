from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from video_edit_automation.application.gaming import GamingHighlightService
from video_edit_automation.application.ports import (
    GameDetector,
    HighlightSignalAnalyzer,
    Repository,
)
from video_edit_automation.application.review import HighlightReviewService
from video_edit_automation.domain.errors import (
    AnalyzerUnavailableError,
    EntityNotFoundError,
    InvalidEditPlanError,
    MediaToolError,
)
from video_edit_automation.domain.gaming import (
    HighlightAnalysis,
    HighlightCandidate,
    HighlightSignal,
)
from video_edit_automation.domain.models import EditBrief, EditPlan, PlanValidationReport
from video_edit_automation.domain.review import HighlightReviewSession
from video_edit_automation.infrastructure.paths import WorkspaceManager


@dataclass(frozen=True, slots=True)
class AutomaticGamingHighlightResult:
    analysis: HighlightAnalysis
    analysis_path: Path
    plan: EditPlan
    validation: PlanValidationReport
    selected_candidates: list[HighlightCandidate]
    review_session: HighlightReviewSession


class AutomaticGamingHighlightService:
    """Turns analyzer evidence into a persisted, validated gaming edit plan."""

    _AUTOMATION_EVENTS = frozenset({"champion_kill", "multi_kill", "team_fight"})

    def __init__(
        self,
        repository: Repository,
        detector: GameDetector,
        analyzer: HighlightSignalAnalyzer,
        gaming: GamingHighlightService,
        reviews: HighlightReviewService,
        workspace: WorkspaceManager,
    ) -> None:
        self.repository = repository
        self.detector = detector
        self.analyzer = analyzer
        self.gaming = gaming
        self.reviews = reviews
        self.workspace = workspace

    def analyze_and_create_plan(
        self,
        project_id: UUID,
        asset_id: UUID,
        brief: EditBrief,
        game_id: str,
        game_profile_id: str | None = None,
        max_highlights: int = 20,
    ) -> AutomaticGamingHighlightResult:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        asset = self.repository.get_asset(asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {asset_id} was not found in project {project_id}"
            )
        game = self.detector.get_game(game_id)
        if game is None:
            raise EntityNotFoundError(f"Game {game_id!r} was not found")
        if not self.analyzer.available():
            raise AnalyzerUnavailableError(
                f"Gaming analyzer {self.analyzer.name!r} is not available on this machine"
            )

        profile_id = game_profile_id or game.default_profile_id
        analysis = self.analyzer.analyze(asset, game).model_copy(
            update={"game_profile_id": profile_id}
        )
        if analysis.asset_id != asset.id or analysis.source_fingerprint != asset.source_fingerprint:
            raise MediaToolError("Gaming analyzer returned evidence for a different media asset")
        if any(signal.asset_id != asset.id for signal in analysis.signals):
            raise MediaToolError("Gaming analyzer returned a signal for a different media asset")
        self.repository.save_highlight_analysis(project_id, analysis)
        analysis_path = self._persist_analysis(project_id, analysis)
        automation_signals = self._automation_signals(analysis.signals)
        if not automation_signals:
            raise InvalidEditPlanError(
                "No kills or team fights were detected. Try Manual mode for this recording."
            )
        plan, validation, selected = self.gaming.create_plan(
            project_id=project_id,
            brief=brief,
            profile_id=profile_id,
            signals=automation_signals,
            max_highlights=max_highlights,
        )
        review_session = self.reviews.create_session(
            project_id=project_id,
            asset_id=asset_id,
            plan=plan,
            analysis=analysis,
            candidates=selected,
        )
        return AutomaticGamingHighlightResult(
            analysis=analysis,
            analysis_path=analysis_path,
            plan=plan,
            validation=validation,
            selected_candidates=selected,
            review_session=review_session,
        )

    @classmethod
    def _automation_signals(
        cls,
        signals: list[HighlightSignal],
    ) -> list[HighlightSignal]:
        return [
            signal
            for signal in signals
            if signal.normalized_event_name in cls._AUTOMATION_EVENTS
        ]

    def _persist_analysis(
        self,
        project_id: UUID,
        analysis: HighlightAnalysis,
    ) -> Path:
        output = self.workspace.gaming_analysis_output(
            project_id,
            analysis.asset_id,
            analysis.id,
        )
        partial = output.with_name(f"{output.name}.{uuid4()}.partial")
        try:
            partial.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
        return output
