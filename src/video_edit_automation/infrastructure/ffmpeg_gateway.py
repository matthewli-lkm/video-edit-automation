from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID

from video_edit_automation.domain.errors import MediaToolError
from video_edit_automation.domain.models import (
    AspectRatio,
    AudioOutputMode,
    AudioTrack,
    AudioTrackRole,
    EditPlan,
    MediaAsset,
    ProbedMedia,
    RenderPreset,
    RenderProfile,
)


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _frame_rate(stream: dict[str, Any]) -> float:
    for field in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(field)
        if not value or value in {"0/0", "N/A"}:
            continue
        try:
            rate = float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    raise MediaToolError("ffprobe did not report a valid video frame rate")


def _even(value: float) -> int:
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded - 1


def _target_frame_rate(preset: RenderPreset, assets: list[MediaAsset]) -> int:
    """Use 60 fps by default only when every source segment can supply it."""
    if preset.frames_per_second != 60:
        return preset.frames_per_second
    return 60 if all(asset.frame_rate >= 59 for asset in assets) else 30


def _infer_audio_role(title: str | None) -> AudioTrackRole:
    normalized = (title or "").strip().lower()
    if not normalized:
        return AudioTrackRole.UNKNOWN
    if "microphone" in normalized or normalized in {"mic", "microphone audio"}:
        return AudioTrackRole.MICROPHONE
    if "voice chat" in normalized or "discord" in normalized or normalized == "chat":
        return AudioTrackRole.VOICE_CHAT
    if "game" in normalized or "desktop" in normalized or "system" in normalized:
        return AudioTrackRole.GAME
    if "music" in normalized:
        return AudioTrackRole.MUSIC
    if "mix" in normalized or "master" in normalized:
        return AudioTrackRole.MIXED
    return AudioTrackRole.UNKNOWN


class FFmpegGateway:
    def __init__(
        self,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        video_codec: str = "libx264",
    ) -> None:
        self.ffmpeg_binary = ffmpeg_binary
        self.ffprobe_binary = ffprobe_binary
        self.video_codec = video_codec

    def available(self) -> bool:
        return bool(shutil.which(self.ffmpeg_binary) and shutil.which(self.ffprobe_binary))

    def probe(self, source_path: Path) -> ProbedMedia:
        command = [
            self.ffprobe_binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(source_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise MediaToolError(f"Unable to start ffprobe: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-2_000:]
            raise MediaToolError(f"ffprobe rejected the media file: {detail}")

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise MediaToolError("ffprobe returned invalid JSON") from exc

        streams = payload.get("streams", [])
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
        audio = audio_streams[0] if audio_streams else None
        if video is None:
            raise MediaToolError("The selected file has no video stream")

        duration = _safe_float(payload.get("format", {}).get("duration"))
        duration = duration or _safe_float(video.get("duration"))
        if duration is None:
            raise MediaToolError("ffprobe did not report a valid media duration")

        audio_tracks = []
        for stream in audio_streams:
            tags = stream.get("tags") or {}
            title = tags.get("title") or tags.get("handler_name")
            audio_tracks.append(
                AudioTrack(
                    stream_index=int(stream["index"]),
                    role=_infer_audio_role(title),
                    codec=stream.get("codec_name"),
                    channels=stream.get("channels"),
                    title=title,
                    language=tags.get("language"),
                )
            )
        if len(audio_tracks) == 1 and audio_tracks[0].role == AudioTrackRole.UNKNOWN:
            audio_tracks[0] = audio_tracks[0].model_copy(update={"role": AudioTrackRole.MIXED})

        return ProbedMedia(
            duration_seconds=duration,
            width=int(video["width"]),
            height=int(video["height"]),
            frame_rate=_frame_rate(video),
            has_video=True,
            has_audio=audio is not None,
            video_codec=video.get("codec_name"),
            audio_codec=audio.get("codec_name") if audio else None,
            audio_tracks=audio_tracks,
        )

    @staticmethod
    def _audio_input(
        input_index: int,
        asset: MediaAsset,
        mode: AudioOutputMode,
    ) -> str:
        if mode == AudioOutputMode.SOURCE_MIX:
            if not asset.audio_tracks:
                return f"[{input_index}:a:0]"
            track = next(
                (
                    candidate
                    for candidate in asset.audio_tracks
                    if candidate.role == AudioTrackRole.MIXED
                ),
                asset.audio_tracks[0],
            )
            return f"[{input_index}:{track.stream_index}]"

        game_track = next(
            (
                candidate
                for candidate in asset.audio_tracks
                if candidate.role == AudioTrackRole.GAME
            ),
            None,
        )
        if game_track is None:
            raise MediaToolError(
                "Game-only export requires a separate audio track assigned the 'game' role. "
                "A mixed track cannot have microphone audio removed reliably after recording."
            )
        return f"[{input_index}:{game_track.stream_index}]"

    def _target_dimensions(
        self,
        preset: RenderPreset,
        first_asset: MediaAsset,
    ) -> tuple[int, int]:
        if preset.aspect_ratio == AspectRatio.LANDSCAPE:
            return (1280, 720) if preset.profile == RenderProfile.PREVIEW else (1920, 1080)
        if preset.aspect_ratio == AspectRatio.VERTICAL:
            return (720, 1280) if preset.profile == RenderProfile.PREVIEW else (1080, 1920)
        if preset.aspect_ratio == AspectRatio.SQUARE:
            return (720, 720) if preset.profile == RenderProfile.PREVIEW else (1080, 1080)

        width, height = first_asset.width, first_asset.height
        if preset.profile == RenderProfile.PREVIEW:
            scale = min(1.0, 1280 / width, 720 / height)
            width, height = _even(width * scale), _even(height * scale)
        return _even(width), _even(height)

    def build_render_command(
        self,
        plan: EditPlan,
        assets: dict[UUID, MediaAsset],
        output_path: Path,
        preset: RenderPreset,
    ) -> list[str]:
        try:
            ordered_assets = [assets[segment.asset_id] for segment in plan.segments]
        except KeyError as exc:
            raise MediaToolError(f"Render plan references unknown asset {exc.args[0]}") from exc
        if any(not asset.has_video for asset in ordered_assets):
            raise MediaToolError("Every v1 render segment must have a video stream")
        if any(not asset.has_audio for asset in ordered_assets):
            raise MediaToolError("Every v1 render segment must have an audio stream")

        width, height = self._target_dimensions(preset, ordered_assets[0])
        frames_per_second = _target_frame_rate(preset, ordered_assets)
        command = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
        ]
        for asset in ordered_assets:
            command.extend(["-i", str(asset.source_path)])

        filters: list[str] = []
        concat_inputs: list[str] = []
        for index, segment in enumerate(plan.segments):
            start = f"{segment.source_in_seconds:.6f}"
            end = f"{segment.source_out_seconds:.6f}"
            filters.append(
                f"[{index}:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={frames_per_second},format=yuv420p[v{index}]"
            )
            audio_input = self._audio_input(index, ordered_assets[index], preset.audio_output_mode)
            filters.append(
                f"{audio_input}atrim=start={start}:end={end},"
                f"asetpts=PTS-STARTPTS,aresample=48000[a{index}]"
            )
            concat_inputs.extend([f"[v{index}]", f"[a{index}]"])
        filters.append(
            "".join(concat_inputs) + f"concat=n={len(plan.segments)}:v=1:a=1[outv][outa]"
        )

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                self.video_codec,
            ]
        )
        if self.video_codec == "libx264":
            command.extend(
                [
                    "-preset",
                    "veryfast" if preset.profile == RenderProfile.PREVIEW else "medium",
                    "-crf",
                    "28" if preset.profile == RenderProfile.PREVIEW else "20",
                ]
            )
        elif self.video_codec == "h264_videotoolbox":
            command.extend(
                [
                    "-allow_sw",
                    "1",
                    "-b:v",
                    "4M" if preset.profile == RenderProfile.PREVIEW else "10M",
                ]
            )
        command.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    def render(
        self,
        plan: EditPlan,
        assets: dict[UUID, MediaAsset],
        output_path: Path,
        preset: RenderPreset,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = self.build_render_command(plan, assets, output_path, preset)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise MediaToolError(f"Unable to start FFmpeg: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-4_000:]
            raise MediaToolError(f"FFmpeg render failed: {detail}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise MediaToolError("FFmpeg exited successfully but did not create a usable output")
        try:
            rendered = self.probe(output_path)
        except MediaToolError as exc:
            raise MediaToolError(
                f"FFmpeg exited successfully but created an invalid output: {exc}"
            ) from exc
        expected_duration = plan.duration_seconds
        duration_tolerance = max(0.5, expected_duration * 0.01)
        if abs(rendered.duration_seconds - expected_duration) > duration_tolerance:
            raise MediaToolError(
                "Rendered duration does not match the validated edit plan: "
                f"expected {expected_duration:.3f}s, got {rendered.duration_seconds:.3f}s"
            )
