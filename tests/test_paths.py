from __future__ import annotations

from pathlib import Path

import pytest

from video_edit_automation.domain.errors import PathNotAllowedError, UnsupportedMediaError
from video_edit_automation.infrastructure.paths import ImportPathPolicy, WorkspaceManager


def test_allowed_media_file_is_resolved(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    video = root / "clip.MOV"
    video.write_bytes(b"clip")
    policy = ImportPathPolicy((root,))
    assert policy.resolve_media_file(video) == video.resolve()


def test_symlink_cannot_escape_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "private.mov"
    outside.write_bytes(b"clip")
    link = root / "looks-safe.mov"
    link.symlink_to(outside)
    policy = ImportPathPolicy((root,))
    with pytest.raises(PathNotAllowedError):
        policy.resolve_media_file(link)


def test_unknown_extension_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("not video")
    policy = ImportPathPolicy((tmp_path,))
    with pytest.raises(UnsupportedMediaError):
        policy.resolve_media_file(source)


def test_new_project_uses_a_named_folder_and_visible_exports_directory(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    project_id = uuid4()
    workspace = WorkspaceManager(
        tmp_path / "app-data",
        tmp_path / "Desktop" / "Cutroom Projects",
    )
    workspace.initialize()

    root = workspace.create_project(project_id, "League: Highlights")
    output = workspace.render_output(
        project_id,
        uuid4(),
        "final",
        filename="Baron / teamfight",
    )

    assert root.name == "League- Highlights"
    assert (root / ".cutroom-project").read_text() == str(project_id)
    assert output.parent == root / "Exports"
    assert output.name.startswith("Baron - teamfight--")


def test_existing_app_data_project_is_kept_when_desktop_projects_are_enabled(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    project_id = uuid4()
    legacy = tmp_path / "app-data" / "projects" / str(project_id)
    legacy.mkdir(parents=True)
    workspace = WorkspaceManager(
        tmp_path / "app-data",
        tmp_path / "Desktop" / "Cutroom Projects",
    )
    workspace.initialize()

    assert workspace.ensure_project(project_id) == legacy.resolve()


def test_named_project_does_not_follow_an_existing_desktop_symlink(tmp_path: Path) -> None:
    from uuid import uuid4

    projects = tmp_path / "Desktop" / "Cutroom Projects"
    projects.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (projects / "League highlights").symlink_to(outside)
    workspace = WorkspaceManager(tmp_path / "app-data", projects)

    root = workspace.create_project(uuid4(), "League highlights")

    assert root.name == "League highlights (2)"
    assert root.parent == projects.resolve()
    assert not (outside / ".cutroom-project").exists()
