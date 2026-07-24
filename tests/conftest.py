from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from video_edit_automation.config import Settings
from video_edit_automation.domain.models import (
    AudioTrack,
    AudioTrackRole,
    EditPlan,
    MediaAsset,
    ProbedMedia,
    RenderPreset,
)
from video_edit_automation.main import create_app


class FakeMediaGateway:
    def available(self) -> bool:
        return True

    def probe(self, source_path: Path) -> ProbedMedia:
        return ProbedMedia(
            duration_seconds=10,
            width=1920,
            height=1080,
            frame_rate=30,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
            audio_tracks=[
                AudioTrack(stream_index=1, role=AudioTrackRole.UNKNOWN, title="Track 1"),
                AudioTrack(stream_index=2, role=AudioTrackRole.UNKNOWN, title="Track 2"),
            ],
        )

    def build_render_command(
        self,
        plan: EditPlan,
        assets: dict[UUID, MediaAsset],
        output_path: Path,
        preset: RenderPreset,
    ) -> list[str]:
        source_path = assets[plan.segments[0].asset_id].source_path
        return ["ffmpeg", "-i", str(source_path), str(output_path)]

    def build_proxy_command(
        self,
        asset: MediaAsset,
        output_path: Path,
        maximum_width: int,
    ) -> list[str]:
        return [
            "ffmpeg",
            "-i",
            str(asset.source_path),
            "-vf",
            f"scale={maximum_width}:-2",
            str(output_path),
        ]

    def create_proxy(
        self,
        asset: MediaAsset,
        output_path: Path,
        maximum_width: int,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake browser-compatible proxy")

    def render(
        self,
        plan: EditPlan,
        assets: dict[UUID, MediaAsset],
        output_path: Path,
        preset: RenderPreset,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake rendered video")


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    return root


@pytest.fixture
def capture_inbox_root(tmp_path: Path) -> Path:
    root = tmp_path / "capture-inbox"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path: Path, media_root: Path, capture_inbox_root: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        allowed_media_roots=[media_root],
        capture_inbox_roots=[capture_inbox_root],
        capture_inbox_auto_scan=False,
        llm_model="",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings=settings, media=FakeMediaGateway())) as test_client:
        yield test_client
