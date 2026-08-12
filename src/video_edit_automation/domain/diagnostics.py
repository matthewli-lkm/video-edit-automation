from __future__ import annotations

from typing import Literal

from pydantic import Field

from video_edit_automation.domain.models import StrictModel


class DependencyDiagnostic(StrictModel):
    name: str
    status: Literal["ready", "unavailable", "error"]
    version: str | None = None


class DesktopDiagnostics(StrictModel):
    status: Literal["ready", "degraded"]
    database: Literal["ready", "unavailable"]
    workspace: Literal["ready", "unavailable"]
    workspace_free_bytes: int = Field(ge=0)
    ffmpeg: DependencyDiagnostic
    ffprobe: DependencyDiagnostic
    tesseract: DependencyDiagnostic
    local_planner: Literal["configured", "disabled"]
    highlight_reviewer: Literal["ready", "disabled", "unavailable"]
