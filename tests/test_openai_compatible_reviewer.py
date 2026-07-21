from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from video_edit_automation.domain.agent_workflow import (
    HighlightReviewerEvidence,
    HighlightReviewFrame,
    HighlightReviewFrameRequest,
    HighlightReviewSegmentEvidence,
)
from video_edit_automation.domain.gaming import HighlightSignal, HighlightSignalType
from video_edit_automation.infrastructure import openai_compatible_reviewer as reviewer_module
from video_edit_automation.infrastructure import review_frame_sampler as sampler_module
from video_edit_automation.infrastructure.openai_compatible_reviewer import (
    OpenAICompatibleHighlightReviewer,
)
from video_edit_automation.infrastructure.review_frame_sampler import FFmpegReviewFrameSampler


def _evidence() -> HighlightReviewerEvidence:
    project_id = uuid4()
    asset_id = uuid4()
    signal = HighlightSignal(
        id="signal-1",
        asset_id=asset_id,
        timestamp_seconds=12,
        signal_type=HighlightSignalType.GAME_EVENT,
        event_name="champion_kill",
        confidence=0.95,
        source="test:ocr",
    )
    return HighlightReviewerEvidence(
        workflow_id=uuid4(),
        review_session_id=uuid4(),
        review_round=1,
        maximum_review_rounds=2,
        project_id=project_id,
        asset_id=asset_id,
        asset_duration_seconds=100,
        current_plan_id=uuid4(),
        plan_title="League highlights",
        plan_summary="One evidence-linked play.",
        expected_render_duration_seconds=20,
        render_job_id=uuid4(),
        rendered_file_size_bytes=1234,
        segments=[
            HighlightReviewSegmentEvidence(
                segment_ref="segment-1",
                segment_index=0,
                asset_id=asset_id,
                source_in_seconds=5,
                source_out_seconds=25,
                output_in_seconds=0,
                output_out_seconds=20,
                purpose="Champion kill",
                signal_ids=[signal.id],
            )
        ],
        signals=[signal],
        frame_requests=[
            HighlightReviewFrameRequest(
                output_timestamp_seconds=7,
                label="segment-1 evidence champion_kill",
            )
        ],
    )


class FakeFrameSampler:
    def available(self) -> bool:
        return True

    def sample(
        self,
        preview_path: Path,
        evidence: HighlightReviewerEvidence,
    ) -> list[HighlightReviewFrame]:
        assert preview_path == Path("/trusted/backend/preview.mp4")
        assert evidence.review_round == 1
        return [
            HighlightReviewFrame(
                output_timestamp_seconds=7,
                label="segment-1 evidence champion_kill",
                jpeg_base64=base64.b64encode(b"jpeg").decode("ascii"),
            )
        ]


def test_reviewer_requests_schema_bound_multimodal_output(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "outcome": "approve",
                                    "confidence": 0.96,
                                    "summary": "The selected play is coherent.",
                                    "corrections": [],
                                }
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, url: str, headers: dict, json: dict) -> FakeResponse:
            captured.update(url=url, headers=headers, payload=json)
            return FakeResponse()

    monkeypatch.setattr(reviewer_module.httpx, "Client", FakeClient)
    reviewer = OpenAICompatibleHighlightReviewer(
        base_url="http://127.0.0.1:8642/v1",
        model="local-vision-model",
        api_key="local",
        timeout_seconds=45,
        frame_sampler=FakeFrameSampler(),
    )

    verdict = reviewer.review(_evidence(), Path("/trusted/backend/preview.mp4"))

    assert verdict.outcome.value == "approve"
    assert captured["url"] == "http://127.0.0.1:8642/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0
    response_format = captured["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    user_content = captured["payload"]["messages"][1]["content"]
    assert any(item["type"] == "image_url" for item in user_content)
    serialized_payload = json.dumps(captured["payload"])
    assert "/trusted/backend/preview.mp4" not in serialized_payload
    assert "upload actions" in serialized_payload


def test_frame_sampler_builds_a_non_shell_ffmpeg_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    preview = tmp_path / "preview with spaces.mp4"
    preview.write_bytes(b"fake preview")
    captured: dict = {}

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, b"jpeg bytes", b"")

    monkeypatch.setattr(sampler_module.subprocess, "run", fake_run)
    sampler = FFmpegReviewFrameSampler(ffmpeg_binary="ffmpeg", width=640)

    frames = sampler.sample(preview, _evidence())

    assert captured["command"] == sampler.build_command(preview, 7)
    assert isinstance(captured["command"], list)
    assert captured["command"][captured["command"].index("-i") + 1] == str(preview)
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["capture_output"] is True
    assert base64.b64decode(frames[0].jpeg_base64) == b"jpeg bytes"
