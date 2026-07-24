from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from video_edit_automation import __version__
from video_edit_automation.api.routes import router
from video_edit_automation.application.ports import (
    EditPlanner,
    HighlightReviewer,
    HighlightSignalAnalyzer,
    MediaGateway,
    Repository,
)
from video_edit_automation.config import Settings
from video_edit_automation.domain.errors import (
    AnalyzerUnavailableError,
    DomainError,
    EntityNotFoundError,
    InvalidAgentWorkflowError,
    InvalidCaptureSessionError,
    InvalidEditPlanError,
    InvalidHighlightReviewError,
    MediaToolError,
    PathNotAllowedError,
    PlannerResponseError,
    PlannerUnavailableError,
    ReviewerResponseError,
    ReviewerUnavailableError,
    UnsupportedMediaError,
)
from video_edit_automation.runtime import build_container


def _status_for_error(error: DomainError) -> int:
    if isinstance(error, EntityNotFoundError):
        return 404
    if isinstance(error, PathNotAllowedError):
        return 403
    if isinstance(error, UnsupportedMediaError):
        return 415
    if isinstance(
        error,
        (AnalyzerUnavailableError, PlannerUnavailableError, ReviewerUnavailableError),
    ):
        return 503
    if isinstance(
        error,
        (
            InvalidCaptureSessionError,
            InvalidEditPlanError,
            InvalidHighlightReviewError,
            InvalidAgentWorkflowError,
            MediaToolError,
            PlannerResponseError,
            ReviewerResponseError,
        ),
    ):
        return 422
    return 400


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    media: MediaGateway | None = None,
    planner: EditPlanner | None = None,
    signal_analyzer: HighlightSignalAnalyzer | None = None,
    reviewer: HighlightReviewer | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = build_container(
            settings,
            repository,
            media,
            planner,
            signal_analyzer,
            reviewer,
        )
        app.state.container = container
        monitor_task: asyncio.Task[None] | None = None
        inbox_status = container.capture_inbox.status()
        if inbox_status.automatic_scan_enabled:
            monitor_task = asyncio.create_task(container.capture_inbox.monitor())
        try:
            yield
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor_task

    app = FastAPI(
        title="Video Edit Automation",
        version=__version__,
        description="Local-first API for validated, AI-assisted video edit decisions.",
        lifespan=lifespan,
    )
    allowed_dashboard_origins = (
        settings.dashboard_allowed_origins
        if settings is not None
        else Settings().dashboard_allowed_origins
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_dashboard_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Range"],
        expose_headers=["Accept-Ranges", "Content-Length", "Content-Range"],
    )

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for_error(error),
            content={"detail": str(error), "error_type": type(error).__name__},
        )

    app.include_router(router)
    return app


app = create_app()
