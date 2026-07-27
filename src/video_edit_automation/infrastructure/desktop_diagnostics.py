from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_edit_automation.application.ports import HighlightReviewer, Repository
from video_edit_automation.config import Settings
from video_edit_automation.domain.diagnostics import (
    DependencyDiagnostic,
    DesktopDiagnostics,
)


@dataclass(slots=True)
class DesktopDiagnosticsService:
    settings: Settings
    repository: Repository
    reviewer: HighlightReviewer | None = None

    @staticmethod
    def _dependency(name: str, executable: str, version_args: list[str]) -> DependencyDiagnostic:
        resolved = shutil.which(executable)
        if resolved is None:
            return DependencyDiagnostic(name=name, status="unavailable")
        try:
            completed = subprocess.run(
                [resolved, *version_args],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return DependencyDiagnostic(name=name, status="error")
        if completed.returncode != 0:
            return DependencyDiagnostic(name=name, status="error")
        version_output = completed.stdout.strip() or completed.stderr.strip()
        version = version_output.splitlines()[0][:160] if version_output else None
        return DependencyDiagnostic(name=name, status="ready", version=version)

    def inspect(self) -> DesktopDiagnostics:
        data_directory = self.settings.data_dir.expanduser().resolve()
        workspace_ready = (
            data_directory.is_dir()
            and os.access(data_directory, os.R_OK)
            and os.access(data_directory, os.W_OK)
        )
        free_bytes = self._free_bytes(data_directory) if workspace_ready else 0
        database_ready = self.repository.ping()
        ffmpeg = self._dependency("FFmpeg", self.settings.ffmpeg_binary, ["-version"])
        ffprobe = self._dependency("ffprobe", self.settings.ffprobe_binary, ["-version"])
        tesseract = self._dependency(
            "Tesseract OCR",
            self.settings.tesseract_binary,
            ["--version"],
        )
        reviewer = "disabled"
        if self.settings.reviewer_enabled:
            reviewer = (
                "ready"
                if self.reviewer is not None and self.reviewer.available()
                else "unavailable"
            )
        required_ready = (
            database_ready
            and workspace_ready
            and ffmpeg.status == "ready"
            and ffprobe.status == "ready"
        )
        return DesktopDiagnostics(
            status="ready" if required_ready else "degraded",
            database="ready" if database_ready else "unavailable",
            workspace="ready" if workspace_ready else "unavailable",
            workspace_free_bytes=free_bytes,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            tesseract=tesseract,
            local_planner="configured" if self.settings.llm_enabled else "disabled",
            highlight_reviewer=reviewer,
        )

    @staticmethod
    def _free_bytes(data_directory: Path) -> int:
        try:
            return shutil.disk_usage(data_directory).free
        except OSError:
            return 0
