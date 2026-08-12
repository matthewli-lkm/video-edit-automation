from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from video_edit_automation.config import Settings
from video_edit_automation.domain.agent_workflow import (
    HighlightCorrectionAction,
    HighlightReviewerCorrection,
    HighlightReviewerEvidence,
    HighlightReviewerOutcome,
    HighlightReviewerVerdict,
)
from video_edit_automation.domain.gaming import (
    GameContext,
    HighlightAnalysis,
    HighlightSignal,
    HighlightSignalType,
)
from video_edit_automation.domain.models import JobStatus, MediaAsset
from video_edit_automation.infrastructure.sqlite_repository import SQLiteRepository
from video_edit_automation.main import create_app


class WorkflowLeagueAnalyzer:
    name = "workflow-test-analyzer"

    def available(self) -> bool:
        return True

    def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis:
        return HighlightAnalysis(
            analyzer=self.name,
            asset_id=asset.id,
            source_fingerprint=asset.source_fingerprint,
            game=game,
            sampled_frame_count=4,
            audio_peak_count=2,
            signals=[
                HighlightSignal(
                    id="workflow-kill",
                    asset_id=asset.id,
                    timestamp_seconds=30,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="champion_kill",
                    confidence=0.95,
                    source="test:ocr",
                ),
                HighlightSignal(
                    id="workflow-victory",
                    asset_id=asset.id,
                    timestamp_seconds=150,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="champion_kill",
                    confidence=1,
                    source="test:ocr",
                ),
                HighlightSignal(
                    id="workflow-objective",
                    asset_id=asset.id,
                    timestamp_seconds=90,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="champion_kill",
                    confidence=0.8,
                    source="test:ocr",
                ),
            ],
        )


class ScriptedReviewer:
    name = "scripted-reviewer"

    def __init__(self, verdicts: list[HighlightReviewerVerdict]) -> None:
        self.verdicts = verdicts
        self.evidence: list[HighlightReviewerEvidence] = []
        self.preview_paths: list[Path] = []

    def available(self) -> bool:
        return True

    def review(
        self,
        evidence: HighlightReviewerEvidence,
        preview_path: Path,
    ) -> HighlightReviewerVerdict:
        self.evidence.append(evidence)
        self.preview_paths.append(preview_path)
        return self.verdicts.pop(0)


class NarrowThenApproveReviewer(ScriptedReviewer):
    def __init__(self) -> None:
        super().__init__([])

    def review(
        self,
        evidence: HighlightReviewerEvidence,
        preview_path: Path,
    ) -> HighlightReviewerVerdict:
        self.evidence.append(evidence)
        self.preview_paths.append(preview_path)
        if len(self.evidence) == 1:
            segment = evidence.segments[0]
            return HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.REVISE,
                confidence=0.9,
                summary="Tighten the first play before approval.",
                corrections=[
                    HighlightReviewerCorrection(
                        action=HighlightCorrectionAction.ADJUST,
                        segment_ref=segment.segment_ref,
                        start_seconds=segment.source_in_seconds + 1,
                        end_seconds=segment.source_out_seconds - 1,
                        reason="Remove quiet setup and trailing downtime.",
                    )
                ],
            )
        return HighlightReviewerVerdict(
            outcome=HighlightReviewerOutcome.APPROVE,
            confidence=0.96,
            summary="The revised reel is coherent and technically valid.",
        )


class AlwaysNarrowReviewer(NarrowThenApproveReviewer):
    def review(
        self,
        evidence: HighlightReviewerEvidence,
        preview_path: Path,
    ) -> HighlightReviewerVerdict:
        self.evidence.append(evidence)
        self.preview_paths.append(preview_path)
        segment = evidence.segments[0]
        return HighlightReviewerVerdict(
            outcome=HighlightReviewerOutcome.REVISE,
            confidence=0.85,
            summary="Request another bounded trim.",
            corrections=[
                HighlightReviewerCorrection(
                    action=HighlightCorrectionAction.ADJUST,
                    segment_ref=segment.segment_ref,
                    start_seconds=segment.source_in_seconds + 0.5,
                    end_seconds=segment.source_out_seconds - 0.5,
                    reason="The boundary can still be tighter.",
                )
            ],
        )


def _create_review(client: TestClient, media_root: Path) -> tuple[dict, dict]:
    analyzer = WorkflowLeagueAnalyzer()
    container = client.app.state.container
    container.signal_analyzer = analyzer
    container.automatic_gaming.analyzer = analyzer

    project = client.post("/api/v1/projects", json={"name": "Agent-reviewed League reel"}).json()
    source = media_root / "agent-workflow-league.mp4"
    source.write_bytes(b"fake long League source")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    ).json()
    saved_asset = container.repository.get_asset(UUID(asset["id"]))
    assert saved_asset is not None
    container.repository.save_asset(saved_asset.model_copy(update={"duration_seconds": 200}))

    automatic = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/gaming/auto-highlight-plans",
        json={
            "brief": {"objective": "Find both strong League plays"},
            "max_highlights": 2,
        },
    )
    assert automatic.status_code == 201
    return project, automatic.json()


def _start_workflow(
    client: TestClient,
    project: dict,
    automatic: dict,
    reviewer: ScriptedReviewer,
    *,
    maximum_review_rounds: int = 2,
) -> dict:
    container = client.app.state.container
    container.reviewer = reviewer
    container.agent_workflows.reviewer = reviewer
    review_id = automatic["review_session"]["id"]
    started = client.post(
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/agent-workflows",
        json={"maximum_review_rounds": maximum_review_rounds},
    )
    assert started.status_code == 202
    workflow_id = started.json()["id"]
    fetched = client.get(f"/api/v1/projects/{project['id']}/gaming/agent-workflows/{workflow_id}")
    assert fetched.status_code == 200
    return fetched.json()


def test_agent_workflow_approves_and_persists_a_preview_review(
    client: TestClient,
    media_root: Path,
    settings: Settings,
) -> None:
    project, automatic = _create_review(client, media_root)
    reviewer = ScriptedReviewer(
        [
            HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.APPROVE,
                confidence=0.97,
                summary="Both selected plays are worth keeping.",
            )
        ]
    )

    workflow = _start_workflow(client, project, automatic, reviewer)

    assert workflow["state"] == "approved"
    assert workflow["initial_plan_id"] == workflow["current_plan_id"]
    assert len(workflow["rounds"]) == 1
    assert workflow["rounds"][0]["verdict"]["outcome"] == "approve"
    assert reviewer.evidence[0].review_round == 1
    assert reviewer.evidence[0].rendered_file_size_bytes > 0
    assert all(path.is_file() for path in reviewer.preview_paths)

    container = client.app.state.container
    job = container.repository.get_job(UUID(workflow["rounds"][0]["render_job_id"]))
    assert job is not None
    assert job.status == JobStatus.SUCCEEDED
    decisions = container.repository.list_highlight_review_decisions(
        UUID(automatic["review_session"]["id"])
    )
    assert {decision.action.value for decision in decisions} == {"accept"}
    assert all(decision.source.value == "reviewer_agent" for decision in decisions)
    assert all(decision.workflow_id == UUID(workflow["id"]) for decision in decisions)

    reopened = SQLiteRepository(settings.database_path)
    persisted = reopened.get_highlight_agent_workflow(UUID(workflow["id"]))
    assert persisted is not None
    assert persisted.state.value == "approved"

    other_project = client.post("/api/v1/projects", json={"name": "Other project"}).json()
    cross_project = client.get(
        f"/api/v1/projects/{other_project['id']}/gaming/agent-workflows/{workflow['id']}"
    )
    assert cross_project.status_code == 404


def test_agent_workflow_applies_a_valid_revision_then_renders_again(
    client: TestClient,
    media_root: Path,
) -> None:
    project, automatic = _create_review(client, media_root)
    reviewer = NarrowThenApproveReviewer()

    workflow = _start_workflow(client, project, automatic, reviewer)

    assert workflow["state"] == "approved"
    assert workflow["current_plan_id"] != workflow["initial_plan_id"]
    assert [round_["verdict"]["outcome"] for round_ in workflow["rounds"]] == [
        "revise",
        "approve",
    ]
    assert [evidence.review_round for evidence in reviewer.evidence] == [1, 2]
    assert reviewer.evidence[1].current_plan_id == UUID(workflow["current_plan_id"])

    container = client.app.state.container
    revised = container.repository.get_plan(UUID(workflow["current_plan_id"]))
    initial = container.repository.get_plan(UUID(workflow["initial_plan_id"]))
    assert revised is not None and initial is not None
    assert revised.version == initial.version + 1
    assert revised.generated_by == "highlight-reviewer:scripted-reviewer:round-1"
    assert revised.segments[0].source_in_seconds == initial.segments[0].source_in_seconds + 1
    assert revised.segments[0].source_out_seconds == initial.segments[0].source_out_seconds - 1
    assert all(
        container.repository.get_job(UUID(round_["render_job_id"])).status == JobStatus.SUCCEEDED
        for round_ in workflow["rounds"]
    )
    snapshot = container.reviews.snapshot(
        UUID(project["id"]), UUID(automatic["review_session"]["id"])
    )
    assert snapshot.metrics.adjusted_candidate_count == 1
    assert snapshot.metrics.accepted_candidate_count == 1
    assert snapshot.metrics.review_complete is True

    calls_before = len(reviewer.evidence)
    rerun = container.agent_workflows.run(UUID(workflow["id"]))
    assert rerun.state.value == "approved"
    assert len(reviewer.evidence) == calls_before


def test_agent_workflow_can_remove_and_add_only_evidence_linked_clips(
    client: TestClient,
    media_root: Path,
) -> None:
    project, automatic = _create_review(client, media_root)

    class ReplaceClipReviewer(ScriptedReviewer):
        def __init__(self) -> None:
            super().__init__([])

        def review(
            self,
            evidence: HighlightReviewerEvidence,
            preview_path: Path,
        ) -> HighlightReviewerVerdict:
            self.evidence.append(evidence)
            self.preview_paths.append(preview_path)
            if len(self.evidence) > 1:
                return HighlightReviewerVerdict(
                    outcome=HighlightReviewerOutcome.APPROVE,
                    confidence=0.94,
                    summary="The replacement clip is supported and the reel is coherent.",
                )
            assert evidence.unselected_signal_ids
            signal_id = evidence.unselected_signal_ids[0]
            signal = next(item for item in evidence.signals if item.id == signal_id)
            return HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.REVISE,
                confidence=0.88,
                summary="Replace the weaker selected clip with omitted evidence.",
                corrections=[
                    HighlightReviewerCorrection(
                        action=HighlightCorrectionAction.REMOVE,
                        segment_ref=evidence.segments[-1].segment_ref,
                        reason="The final selected clip is weaker than the omitted event.",
                    ),
                    HighlightReviewerCorrection(
                        action=HighlightCorrectionAction.ADD,
                        start_seconds=signal.timestamp_seconds - 5,
                        end_seconds=signal.timestamp_seconds + 5,
                        purpose=signal.event_name or "Evidence-linked event",
                        signal_ids=[signal.id],
                        reason="The supplied evidence supports this missed highlight.",
                    ),
                ],
            )

    reviewer = ReplaceClipReviewer()
    workflow = _start_workflow(client, project, automatic, reviewer)

    assert workflow["state"] == "approved"
    assert len(workflow["rounds"]) == 2
    revised = client.app.state.container.repository.get_plan(UUID(workflow["current_plan_id"]))
    assert revised is not None
    added_signal_id = reviewer.evidence[0].unselected_signal_ids[0]
    assert any(segment.highlight_signal_ids == [added_signal_id] for segment in revised.segments)
    snapshot = client.app.state.container.reviews.snapshot(
        UUID(project["id"]), UUID(automatic["review_session"]["id"])
    )
    assert snapshot.metrics.rejected_candidate_count == 1
    assert snapshot.metrics.accepted_candidate_count == 1
    assert snapshot.metrics.missed_highlight_count == 1


@pytest.mark.parametrize("invalid_kind", ["no_op", "duplicate"])
def test_agent_workflow_rejects_noop_or_repeated_corrections(
    client: TestClient,
    media_root: Path,
    invalid_kind: str,
) -> None:
    project, automatic = _create_review(client, media_root)

    class InvalidCorrectionReviewer(ScriptedReviewer):
        def __init__(self) -> None:
            super().__init__([])

        def review(
            self,
            evidence: HighlightReviewerEvidence,
            preview_path: Path,
        ) -> HighlightReviewerVerdict:
            self.evidence.append(evidence)
            self.preview_paths.append(preview_path)
            segment = evidence.segments[0]
            correction = HighlightReviewerCorrection(
                action=HighlightCorrectionAction.ADJUST,
                segment_ref=segment.segment_ref,
                start_seconds=segment.source_in_seconds,
                end_seconds=segment.source_out_seconds,
                reason="This correction changes nothing.",
            )
            corrections = [correction] if invalid_kind == "no_op" else [correction, correction]
            return HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.REVISE,
                confidence=0.8,
                summary="Return an invalid correction set.",
                corrections=corrections,
            )

    workflow = _start_workflow(client, project, automatic, InvalidCorrectionReviewer())

    assert workflow["state"] == "human_review_required"
    expected = "did not change" if invalid_kind == "no_op" else "more than once"
    assert expected in workflow["error"]


@pytest.mark.parametrize("invalid_kind", ["segment", "signal", "time"])
def test_agent_workflow_rejects_untrusted_reviewer_references(
    client: TestClient,
    media_root: Path,
    invalid_kind: str,
) -> None:
    project, automatic = _create_review(client, media_root)

    class InvalidReferenceReviewer(ScriptedReviewer):
        def __init__(self) -> None:
            super().__init__([])

        def review(
            self,
            evidence: HighlightReviewerEvidence,
            preview_path: Path,
        ) -> HighlightReviewerVerdict:
            self.evidence.append(evidence)
            self.preview_paths.append(preview_path)
            segment = evidence.segments[0]
            if invalid_kind == "signal":
                correction = HighlightReviewerCorrection(
                    action=HighlightCorrectionAction.ADD,
                    start_seconds=60,
                    end_seconds=70,
                    purpose="Unsupported event",
                    signal_ids=["invented-signal"],
                    reason="This ID was not supplied by the backend.",
                )
            else:
                correction = HighlightReviewerCorrection(
                    action=HighlightCorrectionAction.ADJUST,
                    segment_ref=(
                        "invented-segment" if invalid_kind == "segment" else segment.segment_ref
                    ),
                    start_seconds=segment.source_in_seconds,
                    end_seconds=(
                        evidence.asset_duration_seconds + 1
                        if invalid_kind == "time"
                        else segment.source_out_seconds
                    ),
                    reason="Attempt an invalid correction.",
                )
            return HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.REVISE,
                confidence=0.8,
                summary="Request an invalid revision.",
                corrections=[correction],
            )

    workflow = _start_workflow(client, project, automatic, InvalidReferenceReviewer())

    assert workflow["state"] == "human_review_required"
    assert len(workflow["rounds"]) == 1
    assert workflow["current_plan_id"] == workflow["initial_plan_id"]
    expected = {
        "segment": "unknown segment",
        "signal": "unknown evidence signal",
        "time": "registered source duration",
    }[invalid_kind]
    assert expected in workflow["error"]


def test_adjustment_must_retain_cited_evidence(
    client: TestClient,
    media_root: Path,
) -> None:
    project, automatic = _create_review(client, media_root)

    class EvidenceDroppingReviewer(ScriptedReviewer):
        def __init__(self) -> None:
            super().__init__([])

        def review(
            self,
            evidence: HighlightReviewerEvidence,
            preview_path: Path,
        ) -> HighlightReviewerVerdict:
            self.evidence.append(evidence)
            self.preview_paths.append(preview_path)
            segment = evidence.segments[0]
            signal_id = segment.signal_ids[0]
            signal = next(item for item in evidence.signals if item.id == signal_id)
            return HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.REVISE,
                confidence=0.7,
                summary="Move the clip away from its evidence.",
                corrections=[
                    HighlightReviewerCorrection(
                        action=HighlightCorrectionAction.ADJUST,
                        segment_ref=segment.segment_ref,
                        start_seconds=signal.timestamp_seconds + 1,
                        end_seconds=signal.timestamp_seconds + 2,
                        reason="This deliberately excludes the cited event.",
                    )
                ],
            )

    workflow = _start_workflow(client, project, automatic, EvidenceDroppingReviewer())

    assert workflow["state"] == "human_review_required"
    assert "retain at least one" in workflow["error"]


def test_agent_workflow_stops_after_the_review_round_limit(
    client: TestClient,
    media_root: Path,
) -> None:
    project, automatic = _create_review(client, media_root)
    reviewer = AlwaysNarrowReviewer()

    workflow = _start_workflow(
        client,
        project,
        automatic,
        reviewer,
        maximum_review_rounds=2,
    )

    assert workflow["state"] == "human_review_required"
    assert len(workflow["rounds"]) == 2
    assert len(reviewer.evidence) == 2
    assert workflow["current_plan_id"] != workflow["initial_plan_id"]
    assert "automatic round limit" in workflow["error"]


def test_render_failure_never_reaches_the_reviewer(
    client: TestClient,
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, automatic = _create_review(client, media_root)
    reviewer = ScriptedReviewer(
        [
            HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.APPROVE,
                confidence=1,
                summary="This verdict must never be requested.",
            )
        ]
    )

    def fail_render(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(client.app.state.container.renders.media, "render", fail_render)
    workflow = _start_workflow(client, project, automatic, reviewer)

    assert workflow["state"] == "technical_failure"
    assert workflow["error"] == "synthetic render failure"
    assert reviewer.evidence == []
    assert workflow["rounds"][0]["verdict"] is None


def test_workflow_requires_a_reviewer_and_preview_render(
    client: TestClient,
    media_root: Path,
) -> None:
    project, automatic = _create_review(client, media_root)
    review_id = automatic["review_session"]["id"]
    url = f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/agent-workflows"

    unavailable = client.post(url, json={})
    assert unavailable.status_code == 503
    assert unavailable.json()["error_type"] == "ReviewerUnavailableError"

    reviewer = ScriptedReviewer([])
    client.app.state.container.agent_workflows.reviewer = reviewer
    final_render = client.post(url, json={"render_preset": {"profile": "final"}})
    assert final_render.status_code == 422
    assert final_render.json()["error_type"] == "InvalidAgentWorkflowError"


@pytest.mark.integration
def test_real_media_agent_workflow_renders_before_review(
    tmp_path: Path,
    media_root: Path,
) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are not installed")
    source = media_root / "real-agent-workflow.mp4"
    generated = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
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

    class ShortMediaAnalyzer:
        name = "short-real-media-analyzer"

        def available(self) -> bool:
            return True

        def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis:
            return HighlightAnalysis(
                analyzer=self.name,
                asset_id=asset.id,
                source_fingerprint=asset.source_fingerprint,
                game=game,
                sampled_frame_count=1,
                audio_peak_count=0,
                signals=[
                    HighlightSignal(
                        id="real-event",
                        asset_id=asset.id,
                        timestamp_seconds=1.5,
                        signal_type=HighlightSignalType.GAME_EVENT,
                        event_name="champion_kill",
                        source="test:real-media",
                    )
                ],
            )

    reviewer = ScriptedReviewer(
        [
            HighlightReviewerVerdict(
                outcome=HighlightReviewerOutcome.APPROVE,
                confidence=1,
                summary="The real rendered preview is valid.",
            )
        ]
    )
    real_settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "real-agent-data",
        allowed_media_roots=[media_root],
        capture_inbox_roots=[],
        capture_inbox_auto_scan=False,
        llm_model="",
        reviewer_model="",
    )
    with TestClient(
        create_app(
            settings=real_settings,
            signal_analyzer=ShortMediaAnalyzer(),
            reviewer=reviewer,
        )
    ) as real_client:
        project = real_client.post("/api/v1/projects", json={"name": "Real workflow"}).json()
        asset = real_client.post(
            f"/api/v1/projects/{project['id']}/assets/import",
            json={"local_path": str(source)},
        ).json()
        automatic = real_client.post(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}/gaming/auto-highlight-plans",
            json={"brief": {"objective": "Keep the real test event"}},
        )
        assert automatic.status_code == 201
        review_id = automatic.json()["review_session"]["id"]
        started = real_client.post(
            f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/agent-workflows",
            json={"maximum_review_rounds": 2},
        )
        assert started.status_code == 202
        workflow = real_client.get(
            f"/api/v1/projects/{project['id']}/gaming/agent-workflows/{started.json()['id']}"
        ).json()

        assert workflow["state"] == "approved"
        assert len(reviewer.preview_paths) == 1
        rendered = real_client.app.state.container.media.probe(reviewer.preview_paths[0])
        assert rendered.duration_seconds == pytest.approx(3, abs=0.15)
        assert rendered.frame_rate == pytest.approx(30, abs=0.1)
