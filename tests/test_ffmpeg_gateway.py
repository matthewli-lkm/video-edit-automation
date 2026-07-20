from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from video_edit_automation.domain.errors import MediaToolError
from video_edit_automation.domain.models import (
    AspectRatio,
    AudioOutputMode,
    AudioTrack,
    AudioTrackRole,
    EditBrief,
    EditPlan,
    MediaAsset,
    RenderPreset,
    TimelineSegment,
)
from video_edit_automation.infrastructure.ffmpeg_gateway import FFmpegGateway


def _asset(project_id, source_path: Path, duration: float = 2) -> MediaAsset:
    return MediaAsset(
        project_id=project_id,
        source_path=source_path,
        source_fingerprint="fingerprint",
        size_bytes=1,
        modified_at_ns=1,
        duration_seconds=duration,
        width=320,
        height=240,
        frame_rate=30,
        has_audio=True,
    )


def _plan(project_id, asset: MediaAsset) -> EditPlan:
    return EditPlan(
        project_id=project_id,
        title="Test plan",
        summary="One segment",
        brief=EditBrief(objective="Test rendering"),
        segments=[
            TimelineSegment(
                asset_id=asset.id,
                source_in_seconds=0.2,
                source_out_seconds=1.2,
            )
        ],
    )


def test_command_uses_argument_list_and_typed_filter(tmp_path: Path) -> None:
    project_id = uuid4()
    suspicious_path = tmp_path / "clip; touch never.mov"
    asset = _asset(project_id, suspicious_path)
    plan = _plan(project_id, asset)
    command = FFmpegGateway().build_render_command(
        plan,
        {asset.id: asset},
        tmp_path / "output.mp4",
        RenderPreset(aspect_ratio=AspectRatio.VERTICAL),
    )
    assert command[0] == "ffmpeg"
    assert str(suspicious_path) in command
    assert "sh" not in command[:2]
    assert "concat=n=1:v=1:a=1" in command[command.index("-filter_complex") + 1]
    assert "scale=720:1280" in command[command.index("-filter_complex") + 1]


def test_default_render_uses_60_fps_for_60_fps_source(tmp_path: Path) -> None:
    project_id = uuid4()
    asset = _asset(project_id, tmp_path / "60-fps.mp4").model_copy(update={"frame_rate": 59.94})
    command = FFmpegGateway().build_render_command(
        _plan(project_id, asset),
        {asset.id: asset},
        tmp_path / "output.mp4",
        RenderPreset(),
    )
    assert "fps=60" in command[command.index("-filter_complex") + 1]


def test_default_render_falls_back_to_30_fps_for_slower_source(tmp_path: Path) -> None:
    project_id = uuid4()
    asset = _asset(project_id, tmp_path / "30-fps.mp4")
    command = FFmpegGateway().build_render_command(
        _plan(project_id, asset),
        {asset.id: asset},
        tmp_path / "output.mp4",
        RenderPreset(),
    )
    assert "fps=30" in command[command.index("-filter_complex") + 1]


def test_game_only_render_selects_explicit_game_track(tmp_path: Path) -> None:
    project_id = uuid4()
    asset = _asset(project_id, tmp_path / "recording.mkv").model_copy(
        update={
            "audio_tracks": [
                AudioTrack(stream_index=1, role=AudioTrackRole.MICROPHONE),
                AudioTrack(stream_index=2, role=AudioTrackRole.GAME),
            ]
        }
    )
    command = FFmpegGateway().build_render_command(
        _plan(project_id, asset),
        {asset.id: asset},
        tmp_path / "game-only.mp4",
        RenderPreset(audio_output_mode=AudioOutputMode.GAME_ONLY),
    )
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[0:2]atrim" in filter_graph
    assert "[0:1]atrim" not in filter_graph


def test_game_only_render_rejects_mixed_audio(tmp_path: Path) -> None:
    project_id = uuid4()
    asset = _asset(project_id, tmp_path / "mixed.mp4").model_copy(
        update={"audio_tracks": [AudioTrack(stream_index=1, role=AudioTrackRole.MIXED)]}
    )
    with pytest.raises(MediaToolError, match="cannot have microphone audio removed reliably"):
        FFmpegGateway().build_render_command(
            _plan(project_id, asset),
            {asset.id: asset},
            tmp_path / "invalid.mp4",
            RenderPreset(audio_output_mode=AudioOutputMode.GAME_ONLY),
        )


def test_render_rejects_nonempty_but_invalid_output(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"incomplete mp4")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    project_id = uuid4()
    asset = _asset(project_id, tmp_path / "source.mp4")
    with pytest.raises(MediaToolError, match="created an invalid output"):
        FFmpegGateway().render(
            _plan(project_id, asset),
            {asset.id: asset},
            tmp_path / "broken.mp4",
            RenderPreset(),
        )


@pytest.mark.integration
@pytest.mark.parametrize(("source_fps", "expected_fps"), [(30, 30), (60, 60)])
def test_real_ffmpeg_probe_and_render(
    tmp_path: Path,
    source_fps: int,
    expected_fps: int,
) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are not installed")
    source = tmp_path / "source.mp4"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate={source_fps}:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert generated.returncode == 0, generated.stderr

    gateway = FFmpegGateway()
    probed = gateway.probe(source)
    assert probed.audio_tracks[0].role == AudioTrackRole.MIXED
    project_id = uuid4()
    asset = MediaAsset(
        **probed.model_dump(),
        project_id=project_id,
        source_path=source,
        source_fingerprint="integration",
        size_bytes=source.stat().st_size,
        modified_at_ns=source.stat().st_mtime_ns,
    )
    output = tmp_path / "rendered.mp4"
    gateway.render(
        _plan(project_id, asset),
        {asset.id: asset},
        output,
        RenderPreset(aspect_ratio=AspectRatio.LANDSCAPE),
    )
    rendered = gateway.probe(output)
    assert output.stat().st_size > 0
    assert rendered.duration_seconds == pytest.approx(1.0, abs=0.15)
    assert rendered.frame_rate == pytest.approx(expected_fps, abs=0.1)


@pytest.mark.integration
def test_real_obs_style_multitrack_recording_renders_game_audio_only(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are not installed")
    source = tmp_path / "obs-recording.mkv"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-metadata:s:a:0",
            "title=Game Audio",
            "-metadata:s:a:1",
            "title=Microphone",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert generated.returncode == 0, generated.stderr

    gateway = FFmpegGateway()
    probed = gateway.probe(source)
    assert [track.role for track in probed.audio_tracks] == [
        AudioTrackRole.GAME,
        AudioTrackRole.MICROPHONE,
    ]
    project_id = uuid4()
    asset = MediaAsset(
        **probed.model_dump(),
        project_id=project_id,
        source_path=source,
        source_fingerprint="obs-multitrack",
        size_bytes=source.stat().st_size,
        modified_at_ns=source.stat().st_mtime_ns,
    )
    output = tmp_path / "game-only.mp4"
    gateway.render(
        _plan(project_id, asset),
        {asset.id: asset},
        output,
        RenderPreset(audio_output_mode=AudioOutputMode.GAME_ONLY),
    )
    rendered = gateway.probe(output)
    assert output.stat().st_size > 0
    assert len(rendered.audio_tracks) == 1
    assert rendered.duration_seconds == pytest.approx(1.0, abs=0.15)
