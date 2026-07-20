from __future__ import annotations

from dataclasses import dataclass

from video_edit_automation.application.automatic_gaming import AutomaticGamingHighlightService
from video_edit_automation.application.capture import CaptureSessionService
from video_edit_automation.application.gaming import GamingHighlightService
from video_edit_automation.application.ports import (
    EditPlanner,
    HighlightSignalAnalyzer,
    MediaGateway,
    Repository,
)
from video_edit_automation.application.services import (
    PlanService,
    PlanValidator,
    ProjectService,
    RenderService,
)
from video_edit_automation.config import Settings
from video_edit_automation.infrastructure.capture_inbox import CaptureInboxScanner
from video_edit_automation.infrastructure.ffmpeg_gateway import FFmpegGateway
from video_edit_automation.infrastructure.game_catalog import RegistryGameDetector
from video_edit_automation.infrastructure.game_profiles import BUILTIN_GAME_PROFILES
from video_edit_automation.infrastructure.league_ocr_analyzer import LeagueOcrSignalAnalyzer
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
    signal_analyzer: HighlightSignalAnalyzer
    automatic_gaming: AutomaticGamingHighlightService
    captures: CaptureSessionService
    capture_inbox: CaptureInboxScanner
    renders: RenderService


def build_container(
    settings: Settings | None = None,
    repository: Repository | None = None,
    media: MediaGateway | None = None,
    planner: EditPlanner | None = None,
    signal_analyzer: HighlightSignalAnalyzer | None = None,
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
    gaming = GamingHighlightService(repository, plans, dict(BUILTIN_GAME_PROFILES))
    signal_analyzer = signal_analyzer or LeagueOcrSignalAnalyzer(
        ffmpeg_binary=settings.ffmpeg_binary,
        tesseract_binary=settings.tesseract_binary,
        audio_peak_limit=settings.gaming_analysis_audio_peak_limit,
        candidate_radius_seconds=settings.gaming_analysis_candidate_radius_seconds,
        ocr_workers=settings.gaming_analysis_ocr_workers,
        tail_seconds=settings.gaming_analysis_tail_seconds,
        tail_interval_seconds=settings.gaming_analysis_tail_interval_seconds,
    )
    captures = CaptureSessionService(
        repository,
        projects,
        detector,
        set(BUILTIN_GAME_PROFILES),
    )
    return Container(
        settings=settings,
        repository=repository,
        media=media,
        workspace=workspace,
        projects=projects,
        plans=plans,
        gaming=gaming,
        signal_analyzer=signal_analyzer,
        automatic_gaming=AutomaticGamingHighlightService(
            repository,
            detector,
            signal_analyzer,
            gaming,
            workspace,
        ),
        captures=captures,
        capture_inbox=CaptureInboxScanner(
            settings.normalized_capture_inbox_roots(),
            projects,
            captures,
            workspace,
            poll_seconds=settings.capture_inbox_poll_seconds,
            automatic_scan_enabled=settings.capture_inbox_auto_scan,
        ),
        renders=RenderService(repository, media, validator, workspace),
    )
