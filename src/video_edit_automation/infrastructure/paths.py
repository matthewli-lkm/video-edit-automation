from __future__ import annotations

from pathlib import Path
from uuid import UUID

from video_edit_automation.domain.errors import (
    PathNotAllowedError,
    UnsupportedMediaError,
)

SUPPORTED_MEDIA_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})


class ImportPathPolicy:
    def __init__(self, allowed_roots: tuple[Path, ...]) -> None:
        self._allowed_roots = tuple(root.expanduser().resolve() for root in allowed_roots)

    def resolve_media_file(self, requested_path: str | Path) -> Path:
        try:
            resolved = Path(requested_path).expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PathNotAllowedError("The requested media file does not exist") from exc

        if not resolved.is_file():
            raise PathNotAllowedError("The requested path is not a regular file")
        if resolved.suffix.lower() not in SUPPORTED_MEDIA_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_MEDIA_SUFFIXES))
            raise UnsupportedMediaError(
                f"Unsupported media extension; expected one of: {supported}"
            )
        if not any(resolved.is_relative_to(root) for root in self._allowed_roots):
            raise PathNotAllowedError(
                "The media file is outside VEA_ALLOWED_MEDIA_ROOTS; "
                "add its parent folder explicitly"
            )
        return resolved


class WorkspaceManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.projects_dir = self.data_dir / "projects"

    def initialize(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def ensure_project(self, project_id: UUID) -> Path:
        root = self.projects_dir / str(project_id)
        for relative in (
            "analysis",
            "proxies",
            "renders/previews",
            "renders/final",
            "logs",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        return root

    def render_output(self, project_id: UUID, job_id: UUID, profile: str) -> Path:
        project_root = self.ensure_project(project_id)
        directory = "previews" if profile == "preview" else "final"
        output = (project_root / "renders" / directory / f"{job_id}.mp4").resolve()
        if not output.is_relative_to(project_root.resolve()):
            raise RuntimeError("Resolved render output escaped the project workspace")
        return output
