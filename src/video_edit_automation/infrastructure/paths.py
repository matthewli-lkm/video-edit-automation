from __future__ import annotations

import re
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
    marker_name = ".cutroom-project"

    def __init__(self, data_dir: Path, projects_dir: Path | None = None) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.legacy_projects_dir = self.data_dir / "projects"
        self.projects_dir = (
            projects_dir.expanduser().resolve()
            if projects_dir is not None
            else self.legacy_projects_dir
        )
        self._project_roots: dict[UUID, Path] = {}

    def initialize(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_project_name(name: str) -> str:
        cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", name).strip(" .")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:80] or "Untitled Cutroom Project"

    def create_project(self, project_id: UUID, name: str) -> Path:
        base_name = self._safe_project_name(name)
        root = self.projects_dir / base_name
        suffix = 2
        while root.exists():
            marker = root / self.marker_name
            if not root.is_symlink() and marker.is_file():
                try:
                    if marker.read_text(encoding="utf-8").strip() == str(project_id):
                        break
                except OSError:
                    pass
            root = self.projects_dir / f"{base_name} ({suffix})"
            suffix += 1
        root.mkdir(parents=True, exist_ok=True)
        (root / self.marker_name).write_text(str(project_id), encoding="utf-8")
        self._project_roots[project_id] = root.resolve()
        return self.ensure_project(project_id)

    def _find_project_root(self, project_id: UUID) -> Path:
        cached = self._project_roots.get(project_id)
        if cached is not None:
            return cached

        legacy_candidate = self.legacy_projects_dir / str(project_id)
        legacy = legacy_candidate.resolve()
        if legacy_candidate.exists() and not legacy_candidate.is_symlink():
            self._project_roots[project_id] = legacy
            return legacy

        if self.projects_dir.exists():
            for child in self.projects_dir.iterdir():
                marker = child / self.marker_name
                if child.is_symlink() or not child.is_dir() or not marker.is_file():
                    continue
                try:
                    marker_id = marker.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if marker_id == str(project_id):
                    resolved = child.resolve()
                    self._project_roots[project_id] = resolved
                    return resolved

        fallback = (self.projects_dir / str(project_id)).resolve()
        self._project_roots[project_id] = fallback
        return fallback

    def ensure_project(self, project_id: UUID) -> Path:
        root = self._find_project_root(project_id)
        for relative in (
            "analysis",
            "imports",
            "proxies",
            "renders/previews",
            "Exports",
            "logs",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        return root

    def managed_import_path(self, project_id: UUID, session_id: UUID, suffix: str) -> Path:
        project_root = self.ensure_project(project_id).resolve()
        imports_root = (project_root / "imports").resolve()
        normalized_suffix = suffix.lower()
        if normalized_suffix not in SUPPORTED_MEDIA_SUFFIXES:
            raise UnsupportedMediaError(f"Unsupported managed import extension: {suffix}")
        output = (imports_root / f"{session_id}{normalized_suffix}").resolve()
        if not output.is_relative_to(imports_root):
            raise RuntimeError("Resolved managed import escaped the project workspace")
        return output

    def resolve_managed_import(self, project_id: UUID, requested_path: Path) -> Path:
        imports_root = (self.ensure_project(project_id) / "imports").resolve()
        try:
            resolved = requested_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PathNotAllowedError("The managed media file does not exist") from exc
        if not resolved.is_file() or not resolved.is_relative_to(imports_root):
            raise PathNotAllowedError("Managed media must be a file inside the project imports")
        if resolved.suffix.lower() not in SUPPORTED_MEDIA_SUFFIXES:
            raise UnsupportedMediaError("Managed media has an unsupported extension")
        return resolved

    def render_output(
        self,
        project_id: UUID,
        job_id: UUID,
        profile: str,
        filename: str | None = None,
    ) -> Path:
        project_root = self.ensure_project(project_id).resolve()
        if profile == "preview":
            output = (project_root / "renders" / "previews" / f"{job_id}.mp4").resolve()
        else:
            safe_name = self._safe_project_name(filename or "Cutroom export")
            output = (project_root / "Exports" / f"{safe_name}--{str(job_id)[:8]}.mp4").resolve()
        if not output.is_relative_to(project_root.resolve()):
            raise RuntimeError("Resolved render output escaped the project workspace")
        return output

    def proxy_output(self, project_id: UUID, proxy_id: UUID) -> Path:
        project_root = self.ensure_project(project_id).resolve()
        proxies_root = (project_root / "proxies").resolve()
        output = (proxies_root / f"{proxy_id}.mp4").resolve()
        if not output.is_relative_to(proxies_root):
            raise RuntimeError("Resolved proxy output escaped the project workspace")
        return output

    def resolve_proxy_output(self, project_id: UUID, requested_path: Path) -> Path:
        proxies_root = (self.ensure_project(project_id) / "proxies").resolve()
        try:
            resolved = requested_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PathNotAllowedError("The browser preview proxy does not exist") from exc
        if not resolved.is_file() or not resolved.is_relative_to(proxies_root):
            raise PathNotAllowedError(
                "Browser preview proxies must stay inside the project workspace"
            )
        if resolved.suffix.lower() != ".mp4":
            raise UnsupportedMediaError("Browser preview proxies must be MP4 files")
        return resolved

    def resolve_render_output(self, project_id: UUID, requested_path: Path) -> Path:
        project_root = self.ensure_project(project_id).resolve()
        allowed_roots = (
            (project_root / "renders" / "previews").resolve(),
            (project_root / "Exports").resolve(),
        )
        try:
            resolved = requested_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PathNotAllowedError("The rendered media file does not exist") from exc
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(project_root)
            or not any(resolved.is_relative_to(root) for root in allowed_roots)
        ):
            raise PathNotAllowedError("Rendered media must stay inside the project workspace")
        if resolved.suffix.lower() != ".mp4":
            raise UnsupportedMediaError("Rendered previews must be MP4 files")
        return resolved

    def gaming_analysis_output(
        self,
        project_id: UUID,
        asset_id: UUID,
        analysis_id: UUID,
    ) -> Path:
        project_root = self.ensure_project(project_id).resolve()
        output = (
            project_root / "analysis" / f"{asset_id}-{analysis_id}-gaming-signals.json"
        ).resolve()
        if not output.is_relative_to(project_root):
            raise RuntimeError("Resolved analysis output escaped the project workspace")
        return output
