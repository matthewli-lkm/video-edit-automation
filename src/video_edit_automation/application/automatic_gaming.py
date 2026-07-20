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
from video_edit_automation.domain.errors import (
    AnalyzerUnavailableError,
    EntityNotFoundError,
)
from video_edit_automation.domain.gaming import HighlightAnalysis, HighlightCandidate
from video_edit_automation.domain.models import EditBrief, EditPlan, PlanValidationReport
from video_edit_automation.infrastructure.paths import WorkspaceManager


@dataclass(frozen=True, slots=True)
class AutomaticGamingHighlightResult:
    analysis: HighlightAnalysis
    analysis_path: Path
    plan: EditPlan
    validation: PlanValidationReport
    selected_candidates: list[HighlightCandidate]


class AutomaticGamingHighlightService:
    """Turns analyzer evidence into a persisted, validated gaming edit plan."""

    def __init__(
        self,
        repository: Repository,
        detector: GameDetector,
        analyzer: HighlightSignalAnalyzer,
        gaming: GamingHighlightService,
        workspace: WorkspaceManager,
    ) -> None:
        self.repository = repository
        self.detector = detector
        self.analyzer = analyzer
        self.gaming = gaming
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

        analysis = self.analyzer.analyze(asset, game)
        analysis_path = self._persist_analysis(project_id, asset_id, analysis)
        plan, validation, selected = self.gaming.create_plan(
            project_id=project_id,
            brief=brief,
            profile_id=game_profile_id or game.default_profile_id,
            signals=analysis.signals,
            max_highlights=max_highlights,
        )
        return AutomaticGamingHighlightResult(
            analysis=analysis,
            analysis_path=analysis_path,
            plan=plan,
            validation=validation,
            selected_candidates=selected,
        )

    def _persist_analysis(
        self,
        project_id: UUID,
        asset_id: UUID,
        analysis: HighlightAnalysis,
    ) -> Path:
        output = self.workspace.gaming_analysis_output(project_id, asset_id)
        partial = output.with_name(f"{output.name}.{uuid4()}.partial")
        try:
            partial.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)
        return output
