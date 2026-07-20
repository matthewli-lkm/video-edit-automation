from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Video Edit Automation"
    return Path.home() / ".video-edit-automation"


def _default_media_roots() -> list[Path]:
    return [Path.home() / name for name in ("Movies", "Desktop", "Downloads")]


class Settings(BaseSettings):
    """Process configuration loaded from VEA_* environment variables or .env."""

    model_config = SettingsConfigDict(
        env_prefix="VEA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: Path = Field(default_factory=_default_data_dir)
    allowed_media_roots: list[Path] = Field(default_factory=_default_media_roots)
    capture_inbox_roots: list[Path] = Field(default_factory=list)
    capture_inbox_poll_seconds: float = Field(default=15, ge=1, le=3_600)
    capture_inbox_auto_scan: bool = True

    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    video_codec: str = "libx264"

    llm_base_url: str = "http://127.0.0.1:1234/v1"
    llm_model: str = ""
    llm_api_key: str = "local"
    llm_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    minimum_segment_seconds: float = Field(default=0.20, gt=0, le=10)
    source_time_tolerance_seconds: float = Field(default=0.05, ge=0, le=1)

    @property
    def database_path(self) -> Path:
        return self.data_dir.expanduser() / "metadata.sqlite3"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_model.strip())

    def normalized_media_roots(self) -> tuple[Path, ...]:
        return tuple(path.expanduser().resolve() for path in self.allowed_media_roots)

    def normalized_capture_inbox_roots(self) -> tuple[Path, ...]:
        return tuple(path.expanduser().resolve() for path in self.capture_inbox_roots)
