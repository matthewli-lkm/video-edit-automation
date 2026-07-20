from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from video_edit_automation.domain.gaming import DetectedGame, GameContext, HighlightSignal
from video_edit_automation.domain.models import (
    EditBrief,
    EditPlan,
    EditPlanDraft,
    Job,
    MediaAsset,
    ProbedMedia,
    Project,
    RenderPreset,
    TranscriptSegment,
)


class Repository(Protocol):
    def initialize(self) -> None: ...

    def ping(self) -> bool: ...

    def save_project(self, project: Project) -> None: ...

    def get_project(self, project_id: UUID) -> Project | None: ...

    def list_projects(self) -> list[Project]: ...

    def save_asset(self, asset: MediaAsset) -> None: ...

    def get_asset(self, asset_id: UUID) -> MediaAsset | None: ...

    def list_assets(self, project_id: UUID) -> list[MediaAsset]: ...

    def save_plan(self, plan: EditPlan) -> None: ...

    def get_plan(self, plan_id: UUID) -> EditPlan | None: ...

    def list_plans(self, project_id: UUID) -> list[EditPlan]: ...

    def save_job(self, job: Job) -> None: ...

    def get_job(self, job_id: UUID) -> Job | None: ...


class MediaGateway(Protocol):
    def available(self) -> bool: ...

    def probe(self, source_path: Path) -> ProbedMedia: ...

    def build_render_command(
        self,
        plan: EditPlan,
        assets: dict[UUID, MediaAsset],
        output_path: Path,
        preset: RenderPreset,
    ) -> list[str]: ...

    def render(
        self,
        plan: EditPlan,
        assets: dict[UUID, MediaAsset],
        output_path: Path,
        preset: RenderPreset,
    ) -> None: ...


class EditPlanner(Protocol):
    @property
    def name(self) -> str: ...

    def create_draft(
        self,
        brief: EditBrief,
        assets: list[MediaAsset],
        transcript: list[TranscriptSegment],
    ) -> EditPlanDraft: ...


class GameDetector(Protocol):
    """Identifies a game without coupling the editor to one operating system."""

    def detect(self, process_name: str, window_title: str | None = None) -> DetectedGame | None: ...


class HighlightSignalAnalyzer(Protocol):
    """Produces evidence; it does not decide or render timeline cuts."""

    def analyze(self, asset: MediaAsset, game: GameContext) -> list[HighlightSignal]: ...
