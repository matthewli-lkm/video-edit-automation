from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from video_edit_automation.application.services import PlanValidator
from video_edit_automation.domain.models import (
    EditBrief,
    EditPlanDraft,
    MediaAsset,
    TimelineSegment,
    ValidationSeverity,
)


def _asset(project_id, *, duration: float = 10, has_audio: bool = True) -> MediaAsset:
    return MediaAsset(
        project_id=project_id,
        source_path=Path("/tmp/source.mov"),
        source_fingerprint="abc",
        size_bytes=100,
        modified_at_ns=1,
        duration_seconds=duration,
        width=1920,
        height=1080,
        frame_rate=30,
        has_audio=has_audio,
    )


def test_valid_plan_can_warn_about_target_without_blocking() -> None:
    project_id = uuid4()
    asset = _asset(project_id)
    draft = EditPlanDraft(
        title="Short",
        summary="A short clip",
        segments=[
            TimelineSegment(
                asset_id=asset.id,
                source_in_seconds=1,
                source_out_seconds=3,
            )
        ],
    )
    report = PlanValidator().validate(
        project_id,
        draft,
        [asset],
        EditBrief(objective="Make a long clip", target_duration_seconds=20),
    )
    assert report.valid is True
    assert report.issues[0].severity == ValidationSeverity.WARNING


def test_missing_audio_and_short_segment_block_rendering() -> None:
    project_id = uuid4()
    asset = _asset(project_id, has_audio=False)
    draft = EditPlanDraft(
        title="Bad",
        summary="Too short and silent",
        segments=[
            TimelineSegment(
                asset_id=asset.id,
                source_in_seconds=1,
                source_out_seconds=1.1,
            )
        ],
    )
    report = PlanValidator(minimum_segment_seconds=0.2).validate(
        project_id,
        draft,
        [asset],
        EditBrief(objective="Make a clip"),
    )
    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"segment_too_short", "missing_audio"}
