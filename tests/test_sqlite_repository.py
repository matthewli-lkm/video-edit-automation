from __future__ import annotations

from pathlib import Path

from video_edit_automation.domain.models import Project
from video_edit_automation.infrastructure.sqlite_repository import SQLiteRepository


def test_project_round_trip(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "metadata.sqlite3")
    repository.initialize()
    project = Project(name="Round trip")
    repository.save_project(project)
    assert repository.get_project(project.id) == project
    assert repository.list_projects() == [project]
    assert repository.ping() is True
