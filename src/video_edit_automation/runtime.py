from __future__ import annotations

from dataclasses import dataclass

from video_edit_automation.application.capture import CaptureSessionService
from video_edit_automation.application.gaming import GamingHighlightService
from video_edit_automation.application.ports import EditPlanner, MediaGateway, Repository
from video_edit_automation.application.services import (
    PlanService,
    PlanValidator,
    ProjectService,
    RenderService,
)
from video_edit_automation.config import Settings
from video_edit_automation.infrastructure.ffmpeg_gateway import FFmpegGateway
from video_edit_automation.infrastructure.game_catalog import RegistryGameDetector
from video_edit_automation.infrastructure.game_profiles import BUILTIN_GAME_PROFILES
from video_edit_automation.infrastructure.openai_compatible_planner import OpenAICompatiblePlanner
from video_edit_automation.infrastructure.paths import ImportPathPolicy, WorkspaceManager
from video_edit_automation.infrastructure.sqlite_repository import SQLiteRepository


@dataclass(slots=True)
class Container:
    settings: Settings
    repository: Repository
    media: MediaGateway
    workspace: WorkspaceManager
    projects: ProjectService
    plans: PlanService
    gaming: GamingHighlightService
    captures: CaptureSessionService
    renders: RenderService


def build_container(
    settings: Settings | None = None,
    repository: Repository | None = None,
    media: MediaGateway | None = None,
    planner: EditPlanner | None = None,
) -> Container:
    settings = settings or Settings()
    repository = repository or SQLiteRepository(settings.database_path)
    media = media or FFmpegGateway(
        ffmpeg_binary=settings.ffmpeg_binary,
        ffprobe_binary=settings.ffprobe_binary,
        video_codec=settings.video_codec,
    )
    workspace = WorkspaceManager(settings.data_dir)
    repository.initialize()
    workspace.initialize()

    if planner is None and settings.llm_enabled:
        planner = OpenAICompatiblePlanner(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    validator = PlanValidator(
        minimum_segment_seconds=settings.minimum_segment_seconds,
        tolerance_seconds=settings.source_time_tolerance_seconds,
    )
    path_policy = ImportPathPolicy(settings.normalized_media_roots())
    plans = PlanService(repository, validator, planner)
    projects = ProjectService(repository, media, path_policy, workspace)
    detector = RegistryGameDetector()
    return Container(
        settings=settings,
        repository=repository,
        media=media,
        workspace=workspace,
        projects=projects,
        plans=plans,
        gaming=GamingHighlightService(repository, plans, dict(BUILTIN_GAME_PROFILES)),
        captures=CaptureSessionService(
            repository,
            projects,
            detector,
            set(BUILTIN_GAME_PROFILES),
        ),
        renders=RenderService(repository, media, validator, workspace),
    )
