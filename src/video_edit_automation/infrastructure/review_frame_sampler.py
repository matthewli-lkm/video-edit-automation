from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

from video_edit_automation.domain.agent_workflow import (
    HighlightReviewerEvidence,
    HighlightReviewFrame,
)
from video_edit_automation.domain.errors import MediaToolError


class FFmpegReviewFrameSampler:
    """Extracts bounded JPEG evidence from a backend-owned preview path."""

    def __init__(self, ffmpeg_binary: str = "ffmpeg", width: int = 640) -> None:
        self.ffmpeg_binary = ffmpeg_binary
        self.width = width

    def available(self) -> bool:
        return bool(shutil.which(self.ffmpeg_binary))

    def build_command(self, preview_path: Path, timestamp_seconds: float) -> list[str]:
        return [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(preview_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={self.width}:-2",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]

    def sample(
        self,
        preview_path: Path,
        evidence: HighlightReviewerEvidence,
    ) -> list[HighlightReviewFrame]:
        if not preview_path.is_file():
            raise MediaToolError("Reviewer preview path does not reference a readable file")
        frames: list[HighlightReviewFrame] = []
        latest_timestamp = max(0.0, evidence.expected_render_duration_seconds - 0.05)
        for request in evidence.frame_requests:
            timestamp = min(request.output_timestamp_seconds, latest_timestamp)
            command = self.build_command(preview_path, timestamp)
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    shell=False,
                )
            except OSError as exc:
                raise MediaToolError(f"Unable to start FFmpeg frame extraction: {exc}") from exc
            if completed.returncode != 0 or not completed.stdout:
                detail = completed.stderr.decode(errors="replace").strip()[-2_000:]
                raise MediaToolError(
                    f"FFmpeg could not extract reviewer frame at {timestamp:.3f}s: {detail}"
                )
            frames.append(
                HighlightReviewFrame(
                    output_timestamp_seconds=timestamp,
                    label=request.label,
                    jpeg_base64=base64.b64encode(completed.stdout).decode("ascii"),
                )
            )
        return frames
