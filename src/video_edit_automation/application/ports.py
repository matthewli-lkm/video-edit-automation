from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from video_edit_automation.domain.agent_workflow import (
    HighlightAgentWorkflow,
    HighlightReviewerEvidence,
    HighlightReviewerVerdict,
    HighlightReviewFrame,
)
from video_edit_automation.domain.capture import (
    CaptureObservation,
    CaptureSession,
    GameCatalogEntry,
)
from video_edit_automation.domain.gaming import (
    DetectedGame,
    GameContext,
    HighlightAnalysis,
)
from video_edit_automation.domain.models import (
    EditBrief,
    EditPlan,
    EditPlanDraft,
    Job,
    MediaAsset,
    MediaProxy,
    ProbedMedia,
    Project,
    RenderPreset,
    TranscriptSegment,
)
from video_edit_automation.domain.review import (
    HighlightHumanReviewState,
    HighlightReviewDecision,
    HighlightReviewSession,
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

    def list_jobs(self, project_id: UUID) -> list[Job]: ...

    def save_media_proxy(self, proxy: MediaProxy) -> None: ...

    def get_media_proxy(self, proxy_id: UUID) -> MediaProxy | None: ...

    def list_media_proxies(self, project_id: UUID) -> list[MediaProxy]: ...

    def save_capture_session(self, session: CaptureSession) -> None: ...

    def get_capture_session(self, session_id: UUID) -> CaptureSession | None: ...

    def list_capture_sessions(self, project_id: UUID) -> list[CaptureSession]: ...

    def save_highlight_analysis(self, project_id: UUID, analysis: HighlightAnalysis) -> None: ...

    def get_highlight_analysis(self, analysis_id: UUID) -> HighlightAnalysis | None: ...

    def list_highlight_analyses(self, project_id: UUID) -> list[HighlightAnalysis]: ...

    def save_highlight_review_session(self, session: HighlightReviewSession) -> None: ...

    def get_highlight_review_session(
        self,
        session_id: UUID,
    ) -> HighlightReviewSession | None: ...

    def list_highlight_review_sessions(
        self,
        project_id: UUID,
    ) -> list[HighlightReviewSession]: ...

    def save_highlight_review_decision(self, decision: HighlightReviewDecision) -> None: ...

    def list_highlight_review_decisions(
        self,
        session_id: UUID,
    ) -> list[HighlightReviewDecision]: ...

    def save_highlight_human_review_state(self, state: HighlightHumanReviewState) -> None: ...

    def get_highlight_human_review_state(
        self,
        session_id: UUID,
    ) -> HighlightHumanReviewState | None: ...

    def list_highlight_human_review_states(
        self,
        project_id: UUID,
    ) -> list[HighlightHumanReviewState]: ...

    def save_highlight_agent_workflow(self, workflow: HighlightAgentWorkflow) -> None: ...

    def get_highlight_agent_workflow(
        self,
        workflow_id: UUID,
    ) -> HighlightAgentWorkflow | None: ...

    def list_highlight_agent_workflows(
        self,
        project_id: UUID,
    ) -> list[HighlightAgentWorkflow]: ...


class MediaGateway(Protocol):
    def available(self) -> bool: ...

    def probe(self, source_path: Path) -> ProbedMedia: ...

    def build_proxy_command(
        self,
        asset: MediaAsset,
        output_path: Path,
        maximum_width: int,
    ) -> list[str]: ...

    def create_proxy(
        self,
        asset: MediaAsset,
        output_path: Path,
        maximum_width: int,
    ) -> None: ...

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

    def list_games(self) -> list[GameCatalogEntry]: ...

    def get_game(self, game_id: str) -> GameCatalogEntry | None: ...

    def detect(self, observation: CaptureObservation) -> DetectedGame | None: ...


class HighlightSignalAnalyzer(Protocol):
    """Produces evidence; it does not decide or render timeline cuts."""

    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis: ...


class HighlightReviewFrameSampler(Protocol):
    def available(self) -> bool: ...

    def sample(
        self,
        preview_path: Path,
        evidence: HighlightReviewerEvidence,
    ) -> list[HighlightReviewFrame]: ...


class HighlightReviewer(Protocol):
    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def review(
        self,
        evidence: HighlightReviewerEvidence,
        preview_path: Path,
    ) -> HighlightReviewerVerdict: ...
