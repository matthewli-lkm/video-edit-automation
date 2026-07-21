from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from video_edit_automation.domain.gaming import (
    GameContext,
    HighlightAnalysis,
    HighlightSignal,
    HighlightSignalType,
)
from video_edit_automation.domain.models import MediaAsset


class ReviewTestAnalyzer:
    name = "review-test-analyzer"

    def available(self) -> bool:
        return True

    def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis:
        return HighlightAnalysis(
            analyzer=self.name,
            asset_id=asset.id,
            source_fingerprint=asset.source_fingerprint,
            game=game,
            sampled_frame_count=2,
            audio_peak_count=0,
            signals=[
                HighlightSignal(
                    id="review-kill-1",
                    asset_id=asset.id,
                    timestamp_seconds=5,
                    signal_type=HighlightSignalType.OCR_EVENT,
                    event_name="champion_kill",
                    confidence=0.95,
                    source="test:tesseract",
                    metadata={"raw_text": "PlayerOne has slain PlayerTwo"},
                )
            ],
        )


def _create_review(
    client: TestClient,
    media_root: Path,
) -> tuple[dict, dict, dict]:
    analyzer = ReviewTestAnalyzer()
    container = client.app.state.container
    container.signal_analyzer = analyzer
    container.automatic_gaming.analyzer = analyzer
    project = client.post("/api/v1/projects", json={"name": "League review"}).json()
    source = media_root / "league-review.mp4"
    source.write_bytes(b"fake League gameplay")
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/gaming/auto-highlight-plans",
        json={"brief": {"objective": "Find League highlights"}},
    )
    assert response.status_code == 201
    return project, asset, response.json()


def test_automatic_analysis_creates_durable_evidence_review_session(
    client: TestClient,
    media_root: Path,
) -> None:
    project, asset, payload = _create_review(client, media_root)
    analysis = payload["analysis"]
    session = payload["review_session"]

    assert session["analysis_id"] == analysis["id"]
    assert session["plan_id"] == payload["plan"]["id"]
    assert session["asset_id"] == asset["id"]
    assert session["game_profile_id"] == "generic_moba"
    assert session["source_fingerprint"] == analysis["source_fingerprint"]
    assert session["signals"][0]["metadata"]["raw_text"] == ("PlayerOne has slain PlayerTwo")
    assert analysis["id"] in Path(payload["analysis_path"]).name

    saved_analysis = client.app.state.container.repository.get_highlight_analysis(analysis["id"])
    assert saved_analysis is not None
    assert str(saved_analysis.id) == analysis["id"]

    listed = client.get(f"/api/v1/projects/{project['id']}/gaming/highlight-reviews")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [session["id"]]

    snapshot = client.get(
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{session['id']}"
    )
    assert snapshot.status_code == 200
    metrics = snapshot.json()["metrics"]
    assert metrics["candidate_count"] == 1
    assert metrics["pending_candidate_count"] == 1
    assert metrics["review_complete"] is False
    assert metrics["provisional"] is True


def test_review_revisions_and_missed_events_update_metrics(
    client: TestClient,
    media_root: Path,
) -> None:
    project, _asset, payload = _create_review(client, media_root)
    session = payload["review_session"]
    review_url = (
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{session['id']}/decisions"
    )
    candidate = session["candidates"][0]

    adjusted = client.post(
        review_url,
        json={
            "action": "adjust",
            "candidate_id": candidate["id"],
            "start_seconds": 1,
            "end_seconds": 8,
            "note": "Cut the setup and dead air",
        },
    )
    assert adjusted.status_code == 201
    metrics = adjusted.json()["metrics"]
    assert metrics["review_complete"] is True
    assert metrics["adjusted_candidate_count"] == 1
    assert metrics["accepted_reel_duration_seconds"] == 7
    assert metrics["total_boundary_adjustment_seconds"] == 3
    assert metrics["mean_boundary_error_seconds"] == 1.5
    assert metrics["manual_correction_count"] == 1

    missed = client.post(
        review_url,
        json={
            "action": "missed_highlight",
            "start_seconds": 8,
            "end_seconds": 9,
            "event_name": "Champion Kill",
            "note": "A second kill was not selected",
        },
    )
    assert missed.status_code == 201
    metrics = missed.json()["metrics"]
    assert metrics["precision"] == 1
    assert metrics["recall"] == 0.5
    assert metrics["f1_score"] == 0.6667
    assert metrics["missed_event_counts"] == {"champion_kill": 1}
    assert metrics["manual_correction_count"] == 2
    assert metrics["correction_rate"] == 1

    accepted_revision = client.post(
        review_url,
        json={"action": "accept", "candidate_id": candidate["id"]},
    )
    assert accepted_revision.status_code == 201
    body = accepted_revision.json()
    assert len(body["decisions"]) == 3
    assert body["latest_candidate_decisions"][0]["revision"] == 2
    assert body["latest_candidate_decisions"][0]["action"] == "accept"
    assert body["metrics"]["adjusted_candidate_count"] == 0
    assert body["metrics"]["accepted_candidate_count"] == 1
    assert body["metrics"]["accepted_reel_duration_seconds"] == 10
    assert body["metrics"]["manual_correction_count"] == 1
    assert body["metrics"]["correction_rate"] == 0.5


def test_review_rejects_unknown_candidates_and_out_of_duration_ranges(
    client: TestClient,
    media_root: Path,
) -> None:
    project, _asset, payload = _create_review(client, media_root)
    session = payload["review_session"]
    review_url = (
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{session['id']}/decisions"
    )

    unknown = client.post(
        review_url,
        json={"action": "reject", "candidate_id": str(uuid4())},
    )
    assert unknown.status_code == 404

    out_of_duration = client.post(
        review_url,
        json={
            "action": "adjust",
            "candidate_id": session["candidates"][0]["id"],
            "start_seconds": 1,
            "end_seconds": 11,
        },
    )
    assert out_of_duration.status_code == 422
    assert out_of_duration.json()["error_type"] == "InvalidHighlightReviewError"

    missing_event_name = client.post(
        review_url,
        json={"action": "missed_highlight", "start_seconds": 2, "end_seconds": 3},
    )
    assert missing_event_name.status_code == 422
