from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, status
from fastapi.responses import FileResponse

from video_edit_automation.api.schemas import (
    AssetImportRequest,
    AudioTrackRolesRequest,
    AutomaticGamingHighlightPlanRequest,
    AutomaticGamingHighlightPlanResponse,
    CaptureSessionCreateRequest,
    CaptureSessionHighlightPlanRequest,
    GameDetectionRequest,
    GamingHighlightPlanRequest,
    GamingHighlightPlanResponse,
    HealthResponse,
    HighlightAgentWorkflowCreateRequest,
    HighlightHumanReviewStateResponse,
    HighlightReviewSnapshotResponse,
    HumanPlanApprovalRequest,
    HumanPlanRevisionRequest,
    PlanCreateRequest,
    PlanGenerateRequest,
    PlanWithValidation,
    ProjectCreateRequest,
    RenderCommandResponse,
    RenderRequest,
)
from video_edit_automation.application.review import HighlightReviewSnapshot
from video_edit_automation.domain.agent_workflow import HighlightAgentWorkflow
from video_edit_automation.domain.capture import (
    CaptureInboxScanReport,
    CaptureInboxStatus,
    CaptureSession,
    GameCatalogEntry,
    ManualBookmarkInput,
)
from video_edit_automation.domain.errors import EntityNotFoundError
from video_edit_automation.domain.gaming import DetectedGame, GameProfile, HighlightSignal
from video_edit_automation.domain.models import (
    EditPlan,
    Job,
    JobStatus,
    MediaAsset,
    PlanValidationReport,
    Project,
)
from video_edit_automation.domain.review import (
    HighlightReviewDecisionInput,
    HighlightReviewSession,
)
from video_edit_automation.runtime import Container

router = APIRouter()


def _container(request: Request) -> Container:
    return request.app.state.container


def _review_response(snapshot: HighlightReviewSnapshot) -> HighlightReviewSnapshotResponse:
    return HighlightReviewSnapshotResponse(
        session=snapshot.session,
        decisions=snapshot.decisions,
        latest_candidate_decisions=snapshot.latest_candidate_decisions,
        metrics=snapshot.metrics,
    )


def _human_review_response(
    project_id: UUID,
    review_id: UUID,
    request: Request,
) -> HighlightHumanReviewStateResponse:
    container = _container(request)
    state = container.human_reviews.get_state(project_id, review_id)
    plan = container.plans.get(project_id, state.current_plan_id)
    return HighlightHumanReviewStateResponse(
        state=state,
        plan=plan,
        validation=container.plans.validate_saved(project_id, plan.id),
    )


@router.get("/healthz", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    container = _container(request)
    database_ok = container.repository.ping()
    media_ok = container.media.available()
    return HealthResponse(
        status="ok" if database_ok and media_ok else "degraded",
        database="ready" if database_ok else "unavailable",
        media_tools="ready" if media_ok else "unavailable",
        local_planner="configured" if container.settings.llm_enabled else "disabled",
        gaming_analyzer="ready" if container.signal_analyzer.available() else "unavailable",
        highlight_reviewer=(
            "ready"
            if container.reviewer is not None and container.reviewer.available()
            else "disabled"
        ),
    )


@router.get("/api/v1/capture-inbox/status", response_model=CaptureInboxStatus)
def capture_inbox_status(request: Request) -> CaptureInboxStatus:
    return _container(request).capture_inbox.status()


@router.post("/api/v1/capture-inbox/scan", response_model=CaptureInboxScanReport)
def scan_capture_inbox(request: Request) -> CaptureInboxScanReport:
    return _container(request).capture_inbox.scan()


@router.post(
    "/api/v1/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
)
def create_project(payload: ProjectCreateRequest, request: Request) -> Project:
    return _container(request).projects.create(payload.name)


@router.get("/api/v1/projects", response_model=list[Project])
def list_projects(request: Request) -> list[Project]:
    return _container(request).projects.list()


@router.get("/api/v1/projects/{project_id}", response_model=Project)
def get_project(project_id: UUID, request: Request) -> Project:
    return _container(request).projects.get(project_id)


@router.post(
    "/api/v1/projects/{project_id}/assets/import",
    response_model=MediaAsset,
    status_code=status.HTTP_201_CREATED,
)
def import_asset(project_id: UUID, payload: AssetImportRequest, request: Request) -> MediaAsset:
    return _container(request).projects.import_asset(project_id, payload.local_path)


@router.get("/api/v1/projects/{project_id}/assets", response_model=list[MediaAsset])
def list_assets(project_id: UUID, request: Request) -> list[MediaAsset]:
    return _container(request).projects.list_assets(project_id)


@router.get(
    "/api/v1/projects/{project_id}/assets/{asset_id}/media",
    response_class=FileResponse,
)
def get_asset_media(project_id: UUID, asset_id: UUID, request: Request) -> FileResponse:
    source = _container(request).projects.resolve_asset_media(project_id, asset_id)
    return FileResponse(source)


@router.put(
    "/api/v1/projects/{project_id}/assets/{asset_id}/audio-tracks",
    response_model=MediaAsset,
)
def assign_audio_track_roles(
    project_id: UUID,
    asset_id: UUID,
    payload: AudioTrackRolesRequest,
    request: Request,
) -> MediaAsset:
    return _container(request).projects.assign_audio_track_roles(
        project_id,
        asset_id,
        payload.assignments,
    )


@router.get("/api/v1/gaming/profiles", response_model=list[GameProfile])
def list_gaming_profiles(request: Request) -> list[GameProfile]:
    return _container(request).gaming.list_profiles()


@router.get("/api/v1/gaming/games", response_model=list[GameCatalogEntry])
def list_supported_games(request: Request) -> list[GameCatalogEntry]:
    return _container(request).captures.list_games()


@router.post("/api/v1/gaming/detect-game", response_model=DetectedGame | None)
def detect_game(payload: GameDetectionRequest, request: Request) -> DetectedGame | None:
    return _container(request).captures.detect_game(payload.observation)


@router.post(
    "/api/v1/projects/{project_id}/capture-sessions",
    response_model=CaptureSession,
    status_code=status.HTTP_201_CREATED,
)
def create_capture_session(
    project_id: UUID,
    payload: CaptureSessionCreateRequest,
    request: Request,
) -> CaptureSession:
    return _container(request).captures.create(
        project_id=project_id,
        asset_id=payload.asset_id,
        platform=payload.platform,
        recorder=payload.recorder,
        clock_origin_monotonic_ns=payload.clock_origin_monotonic_ns,
        observations=payload.observations,
        audio_track_roles=payload.audio_track_roles,
        game_id_override=payload.game_id_override,
        game_profile_id=payload.game_profile_id,
    )


@router.get(
    "/api/v1/projects/{project_id}/capture-sessions",
    response_model=list[CaptureSession],
)
def list_capture_sessions(project_id: UUID, request: Request) -> list[CaptureSession]:
    return _container(request).captures.list(project_id)


@router.get(
    "/api/v1/projects/{project_id}/capture-sessions/{session_id}",
    response_model=CaptureSession,
)
def get_capture_session(
    project_id: UUID,
    session_id: UUID,
    request: Request,
) -> CaptureSession:
    return _container(request).captures.get(project_id, session_id)


@router.post(
    "/api/v1/projects/{project_id}/capture-sessions/{session_id}/bookmarks",
    response_model=HighlightSignal,
    status_code=status.HTTP_201_CREATED,
)
def add_capture_bookmark(
    project_id: UUID,
    session_id: UUID,
    payload: ManualBookmarkInput,
    request: Request,
) -> HighlightSignal:
    return _container(request).captures.add_bookmark(project_id, session_id, payload)


@router.post(
    "/api/v1/projects/{project_id}/capture-sessions/{session_id}/highlight-plans",
    response_model=GamingHighlightPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_capture_highlight_plan(
    project_id: UUID,
    session_id: UUID,
    payload: CaptureSessionHighlightPlanRequest,
    request: Request,
) -> GamingHighlightPlanResponse:
    container = _container(request)
    profile_id, signals = container.captures.highlight_inputs(project_id, session_id)
    plan, report, selected = container.gaming.create_plan(
        project_id,
        payload.brief,
        profile_id,
        signals,
        payload.max_highlights,
    )
    return GamingHighlightPlanResponse(
        plan=plan,
        validation=report,
        selected_candidates=selected,
    )


@router.post(
    "/api/v1/projects/{project_id}/gaming/highlight-plans",
    response_model=GamingHighlightPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_gaming_highlight_plan(
    project_id: UUID,
    payload: GamingHighlightPlanRequest,
    request: Request,
) -> GamingHighlightPlanResponse:
    plan, report, selected = _container(request).gaming.create_plan(
        project_id,
        payload.brief,
        payload.game_profile_id,
        payload.signals,
        payload.max_highlights,
    )
    return GamingHighlightPlanResponse(
        plan=plan,
        validation=report,
        selected_candidates=selected,
    )


@router.post(
    "/api/v1/projects/{project_id}/assets/{asset_id}/gaming/auto-highlight-plans",
    response_model=AutomaticGamingHighlightPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_automatic_gaming_highlight_plan(
    project_id: UUID,
    asset_id: UUID,
    payload: AutomaticGamingHighlightPlanRequest,
    request: Request,
) -> AutomaticGamingHighlightPlanResponse:
    result = _container(request).automatic_gaming.analyze_and_create_plan(
        project_id=project_id,
        asset_id=asset_id,
        brief=payload.brief,
        game_id=payload.game_id,
        game_profile_id=payload.game_profile_id,
        max_highlights=payload.max_highlights,
    )
    return AutomaticGamingHighlightPlanResponse(
        analysis=result.analysis,
        analysis_path=result.analysis_path,
        plan=result.plan,
        validation=result.validation,
        selected_candidates=result.selected_candidates,
        review_session=result.review_session,
    )


@router.get(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews",
    response_model=list[HighlightReviewSession],
)
def list_highlight_reviews(
    project_id: UUID,
    request: Request,
) -> list[HighlightReviewSession]:
    return _container(request).reviews.list_sessions(project_id)


@router.get(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews/{review_id}",
    response_model=HighlightReviewSnapshotResponse,
)
def get_highlight_review(
    project_id: UUID,
    review_id: UUID,
    request: Request,
) -> HighlightReviewSnapshotResponse:
    return _review_response(_container(request).reviews.snapshot(project_id, review_id))


@router.post(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews/{review_id}/decisions",
    response_model=HighlightReviewSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_highlight_review_decision(
    project_id: UUID,
    review_id: UUID,
    payload: HighlightReviewDecisionInput,
    request: Request,
) -> HighlightReviewSnapshotResponse:
    snapshot = _container(request).reviews.record_decision(project_id, review_id, payload)
    return _review_response(snapshot)


@router.get(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews/{review_id}/human-review",
    response_model=HighlightHumanReviewStateResponse,
)
def get_highlight_human_review(
    project_id: UUID,
    review_id: UUID,
    request: Request,
) -> HighlightHumanReviewStateResponse:
    return _human_review_response(project_id, review_id, request)


@router.post(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews/{review_id}/human-review/revisions",
    response_model=HighlightHumanReviewStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def revise_highlight_plan(
    project_id: UUID,
    review_id: UUID,
    payload: HumanPlanRevisionRequest,
    request: Request,
) -> HighlightHumanReviewStateResponse:
    _container(request).human_reviews.revise(
        project_id,
        review_id,
        payload.expected_plan_id,
        payload.draft,
    )
    return _human_review_response(project_id, review_id, request)


@router.post(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews/{review_id}/human-review/approval",
    response_model=HighlightHumanReviewStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def approve_highlight_plan(
    project_id: UUID,
    review_id: UUID,
    payload: HumanPlanApprovalRequest,
    request: Request,
) -> HighlightHumanReviewStateResponse:
    _container(request).human_reviews.approve(
        project_id,
        review_id,
        payload.plan_id,
        payload.plan_version,
    )
    return _human_review_response(project_id, review_id, request)


@router.post(
    "/api/v1/projects/{project_id}/gaming/highlight-reviews/{review_id}/agent-workflows",
    response_model=HighlightAgentWorkflow,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_highlight_agent_workflow(
    project_id: UUID,
    review_id: UUID,
    payload: HighlightAgentWorkflowCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> HighlightAgentWorkflow:
    container = _container(request)
    workflow = container.agent_workflows.create(
        project_id,
        review_id,
        payload.render_preset,
        payload.maximum_review_rounds,
    )
    background_tasks.add_task(container.agent_workflows.run, workflow.id)
    return workflow


@router.get(
    "/api/v1/projects/{project_id}/gaming/agent-workflows",
    response_model=list[HighlightAgentWorkflow],
)
def list_highlight_agent_workflows(
    project_id: UUID,
    request: Request,
) -> list[HighlightAgentWorkflow]:
    return _container(request).agent_workflows.list(project_id)


@router.get(
    "/api/v1/projects/{project_id}/gaming/agent-workflows/{workflow_id}",
    response_model=HighlightAgentWorkflow,
)
def get_highlight_agent_workflow(
    project_id: UUID,
    workflow_id: UUID,
    request: Request,
) -> HighlightAgentWorkflow:
    return _container(request).agent_workflows.get(project_id, workflow_id)


@router.post(
    "/api/v1/projects/{project_id}/edit-plans",
    response_model=PlanWithValidation,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_plan(
    project_id: UUID,
    payload: PlanCreateRequest,
    request: Request,
) -> PlanWithValidation:
    plan, report = _container(request).plans.create_manual(
        project_id,
        payload.brief,
        payload.draft,
    )
    return PlanWithValidation(plan=plan, validation=report)


@router.post(
    "/api/v1/projects/{project_id}/edit-plans/generate",
    response_model=PlanWithValidation,
    status_code=status.HTTP_201_CREATED,
)
def generate_plan(
    project_id: UUID,
    payload: PlanGenerateRequest,
    request: Request,
) -> PlanWithValidation:
    plan, report = _container(request).plans.generate(
        project_id,
        payload.brief,
        payload.transcript,
    )
    return PlanWithValidation(plan=plan, validation=report)


@router.get("/api/v1/projects/{project_id}/edit-plans", response_model=list[EditPlan])
def list_plans(project_id: UUID, request: Request) -> list[EditPlan]:
    return _container(request).plans.list(project_id)


@router.get(
    "/api/v1/projects/{project_id}/edit-plans/{plan_id}/validation",
    response_model=PlanValidationReport,
)
def validate_plan(project_id: UUID, plan_id: UUID, request: Request) -> PlanValidationReport:
    return _container(request).plans.validate_saved(project_id, plan_id)


@router.post(
    "/api/v1/projects/{project_id}/edit-plans/{plan_id}/renders/dry-run",
    response_model=RenderCommandResponse,
)
def dry_run_render(
    project_id: UUID,
    plan_id: UUID,
    payload: RenderRequest,
    request: Request,
) -> RenderCommandResponse:
    command = _container(request).renders.dry_run(project_id, plan_id, payload.preset)
    return RenderCommandResponse(argv=command, executable=command[0])


@router.post(
    "/api/v1/projects/{project_id}/edit-plans/{plan_id}/renders",
    response_model=Job,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_render(
    project_id: UUID,
    plan_id: UUID,
    payload: RenderRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Job:
    container = _container(request)
    job = container.renders.queue(project_id, plan_id, payload.preset)
    background_tasks.add_task(container.renders.run, job.id)
    return job


@router.get("/api/v1/jobs/{job_id}", response_model=Job)
def get_job(job_id: UUID, request: Request) -> Job:
    return _container(request).renders.get_job(job_id)


@router.get("/api/v1/jobs/{job_id}/media", response_class=FileResponse)
def get_job_media(job_id: UUID, request: Request) -> FileResponse:
    container = _container(request)
    job = container.renders.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED or job.output_path is None:
        raise EntityNotFoundError(f"Rendered media for job {job_id} is not available")
    output = container.workspace.resolve_render_output(job.project_id, job.output_path)
    return FileResponse(output, media_type="video/mp4")
