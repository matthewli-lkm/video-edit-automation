from __future__ import annotations

from pathlib import Path

import pytest

from video_edit_automation.domain.errors import PathNotAllowedError, UnsupportedMediaError
from video_edit_automation.infrastructure.paths import ImportPathPolicy


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
