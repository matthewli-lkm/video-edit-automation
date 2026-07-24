from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from video_edit_automation.domain.models import (
    EditBrief,
    MediaAsset,
    TranscriptSegment,
)
from video_edit_automation.infrastructure import openai_compatible_planner as planner_module
from video_edit_automation.infrastructure.openai_compatible_planner import OpenAICompatiblePlanner


def test_planner_requests_schema_bound_output(monkeypatch) -> None:
    project_id = uuid4()
    asset = MediaAsset(
        project_id=project_id,
        source_path=Path("/tmp/lecture.mov"),
        source_fingerprint="abc",
        size_bytes=100,
        modified_at_ns=1,
        duration_seconds=10,
        width=1920,
        height=1080,
        frame_rate=30,
        has_audio=True,
    )
    response_content = json.dumps(
        {
            "title": "Short explanation",
            "summary": "The clearest sentence.",
            "segments": [
                {
                    "asset_id": str(asset.id),
                    "source_in_seconds": 1,
                    "source_out_seconds": 3,
                    "purpose": "Key point",
                    "transcript_segment_ids": ["t-1"],
                }
            ],
        }
    )
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": response_content}}]}

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

    monkeypatch.setattr(planner_module.httpx, "Client", FakeClient)
    planner = OpenAICompatiblePlanner(
        base_url="http://127.0.0.1:1234/v1",
        model="local-instruct",
        api_key="local",
        timeout_seconds=30,
    )
    draft = planner.create_draft(
        EditBrief(objective="Explain the main result", target_duration_seconds=2),
        [asset],
        [TranscriptSegment(id="t-1", start_seconds=1, end_seconds=3, text="Main result")],
    )

    assert draft.segments[0].asset_id == asset.id
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True
