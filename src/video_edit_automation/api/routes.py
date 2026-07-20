from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, status

from video_edit_automation.api.schemas import (
    AssetImportRequest,
    AudioTrackRolesRequest,
    GamingHighlightPlanRequest,
    GamingHighlightPlanResponse,
    HealthResponse,
    PlanCreateRequest,
    PlanGenerateRequest,
    PlanWithValidation,
    ProjectCreateRequest,
    RenderCommandResponse,
    RenderRequest,
)
from video_edit_automation.domain.gaming import GameProfile
from video_edit_automation.domain.models import (
    EditPlan,
    Job,
    MediaAsset,
    PlanValidationReport,
    Project,
)
from video_edit_automation.runtime import Container

router = APIRouter()


def _container(request: Request) -> Container:
    return request.app.state.container


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
    )


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
