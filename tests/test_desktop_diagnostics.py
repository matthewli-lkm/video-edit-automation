from __future__ import annotations

import subprocess
from pathlib import Path

from video_edit_automation.config import Settings
from video_edit_automation.infrastructure.desktop_diagnostics import (
    DesktopDiagnosticsService,
)


class PingRepository:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def ping(self) -> bool:
        return self.ready


def test_desktop_diagnostics_reports_required_and_optional_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_directory = tmp_path / "desktop-data"
    data_directory.mkdir()
    settings = Settings(
        _env_file=None,
        data_dir=data_directory,
        allowed_media_roots=[tmp_path],
        capture_inbox_auto_scan=False,
    )

    def fake_which(executable: str) -> str | None:
        return None if executable == "tesseract" else f"/tools/{executable}"

    def fake_run(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout=f"{Path(args[0]).name} 1.2.3\n")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.run", fake_run)

    diagnostics = DesktopDiagnosticsService(settings, PingRepository()).inspect()

    assert diagnostics.status == "ready"
    assert diagnostics.database == "ready"
    assert diagnostics.workspace == "ready"
    assert diagnostics.workspace_free_bytes > 0
    assert diagnostics.ffmpeg.status == "ready"
    assert diagnostics.ffprobe.status == "ready"
    assert diagnostics.tesseract.status == "unavailable"
    assert diagnostics.local_planner == "disabled"
    assert diagnostics.highlight_reviewer == "disabled"


def test_desktop_diagnostics_degrades_when_a_required_tool_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_directory = tmp_path / "desktop-data"
    data_directory.mkdir()
    settings = Settings(
        _env_file=None,
        data_dir=data_directory,
        allowed_media_roots=[tmp_path],
        capture_inbox_auto_scan=False,
    )
    monkeypatch.setattr("shutil.which", lambda executable: f"/tools/{executable}")
    monkeypatch.setattr(
        "subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args,
            1 if Path(args[0]).name == "ffprobe" else 0,
            stdout="tool 1.2.3\n",
        ),
    )

    diagnostics = DesktopDiagnosticsService(settings, PingRepository()).inspect()

    assert diagnostics.status == "degraded"
    assert diagnostics.ffprobe.status == "error"
