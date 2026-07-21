from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from video_edit_automation.config import Settings
from video_edit_automation.domain.gaming import (
    GameContext,
    HighlightAnalysis,
    HighlightSignal,
    HighlightSignalType,
)
from video_edit_automation.domain.models import MediaAsset
from video_edit_automation.infrastructure.sqlite_repository import SQLiteRepository


class TwoPlayLeagueAnalyzer:
    name = "two-play-league-analyzer"

    def available(self) -> bool:
        return True

    def analyze(self, asset: MediaAsset, game: GameContext) -> HighlightAnalysis:
        return HighlightAnalysis(
            analyzer=self.name,
            asset_id=asset.id,
            source_fingerprint=asset.source_fingerprint,
            game=game,
            sampled_frame_count=6,
            audio_peak_count=2,
            signals=[
                HighlightSignal(
                    id="review-kill",
                    asset_id=asset.id,
                    timestamp_seconds=30,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="champion_kill",
                    confidence=0.95,
                    source="test:ocr",
                    metadata={"raw_text": "PlayerOne has slain PlayerTwo"},
                ),
                HighlightSignal(
                    id="review-victory",
                    asset_id=asset.id,
                    timestamp_seconds=150,
                    signal_type=HighlightSignalType.GAME_EVENT,
                    event_name="victory",
                    confidence=1,
                    source="test:ocr",
                    metadata={"raw_text": "VICTORY"},
                ),
            ],
        )


def _create_two_candidate_review(
    client: TestClient,
    media_root: Path,
) -> tuple[dict, dict, dict]:
    analyzer = TwoPlayLeagueAnalyzer()
    container = client.app.state.container
    container.signal_analyzer = analyzer
    container.automatic_gaming.analyzer = analyzer

    project = client.post("/api/v1/projects", json={"name": "Review League highlights"}).json()
    source = media_root / "review-league.mp4"
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
    return project, asset, automatic.json()


def test_review_decisions_keep_history_and_compute_metrics(
    client: TestClient,
    media_root: Path,
    settings: Settings,
) -> None:
    project, _asset, automatic = _create_two_candidate_review(client, media_root)
    review = automatic["review_session"]
    review_id = review["id"]
    candidates = sorted(review["candidates"], key=lambda item: item["plan_segment_index"])

    assert review["analysis_id"] == automatic["analysis"]["id"]
    assert review["game_profile_id"] == "generic_moba"
    assert len(candidates) == 2
    assert {signal["id"] for signal in review["signals"]} == {
        "review-kill",
        "review-victory",
    }

    initial = client.get(f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}")
    assert initial.status_code == 200
    assert initial.json()["metrics"] == {
        "review_session_id": review_id,
        "candidate_count": 2,
        "reviewed_candidate_count": 0,
        "pending_candidate_count": 2,
        "accepted_candidate_count": 0,
        "adjusted_candidate_count": 0,
        "rejected_candidate_count": 0,
        "missed_highlight_count": 0,
        "review_complete": False,
        "provisional": True,
        "precision": None,
        "recall": None,
        "f1_score": None,
        "accepted_reel_duration_seconds": 0,
        "total_boundary_adjustment_seconds": 0,
        "mean_boundary_error_seconds": None,
        "manual_correction_count": 0,
        "correction_rate": 0,
        "missed_event_counts": {},
    }

    accept = client.post(
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/decisions",
        json={"action": "accept", "candidate_id": candidates[0]["id"]},
    )
    assert accept.status_code == 201
    assert accept.json()["metrics"]["pending_candidate_count"] == 1

    adjust = client.post(
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/decisions",
        json={
            "action": "adjust",
            "candidate_id": candidates[0]["id"],
            "start_seconds": 10,
            "end_seconds": 45,
            "note": "Start before the engage and keep the reaction",
        },
    )
    assert adjust.status_code == 201
    adjusted_payload = adjust.json()
    assert [decision["revision"] for decision in adjusted_payload["decisions"]] == [1, 2]
    assert len(adjusted_payload["latest_candidate_decisions"]) == 1
    assert adjusted_payload["latest_candidate_decisions"][0]["action"] == "adjust"
    assert adjusted_payload["metrics"]["total_boundary_adjustment_seconds"] == 5
    assert adjusted_payload["metrics"]["mean_boundary_error_seconds"] == 2.5
    assert adjusted_payload["metrics"]["accepted_reel_duration_seconds"] == 35

    reject = client.post(
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/decisions",
        json={"action": "reject", "candidate_id": candidates[1]["id"]},
    )
    assert reject.status_code == 201
    assert reject.json()["metrics"]["review_complete"] is True
    assert reject.json()["metrics"]["precision"] == 0.5
    assert reject.json()["metrics"]["recall"] == 1

    missed = client.post(
        f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review_id}/decisions",
        json={
            "action": "missed_highlight",
            "start_seconds": 170,
            "end_seconds": 180,
            "event_name": "Baron Steal",
        },
    )
    assert missed.status_code == 201
    metrics = missed.json()["metrics"]
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["f1_score"] == 0.5
    assert metrics["manual_correction_count"] == 3
    assert metrics["correction_rate"] == 1
    assert metrics["missed_event_counts"] == {"baron_steal": 1}
    assert len(missed.json()["decisions"]) == 4

    reopened = SQLiteRepository(settings.database_path)
    saved_review = reopened.get_highlight_review_session(UUID(review_id))
    assert saved_review is not None
    assert saved_review.analysis_id == UUID(automatic["analysis"]["id"])
    assert len(reopened.list_highlight_review_decisions(UUID(review_id))) == 4


def test_review_rejects_invalid_ranges_and_cross_project_access(
    client: TestClient,
    media_root: Path,
) -> None:
    project, _asset, automatic = _create_two_candidate_review(client, media_root)
    review = automatic["review_session"]
    candidate = review["candidates"][0]
    review_url = f"/api/v1/projects/{project['id']}/gaming/highlight-reviews/{review['id']}"

    out_of_bounds = client.post(
        f"{review_url}/decisions",
        json={
            "action": "adjust",
            "candidate_id": candidate["id"],
            "start_seconds": 10,
            "end_seconds": 201,
        },
    )
    assert out_of_bounds.status_code == 422
    assert out_of_bounds.json()["error_type"] == "InvalidHighlightReviewError"

    missing_event_name = client.post(
        f"{review_url}/decisions",
        json={
            "action": "missed_highlight",
            "start_seconds": 170,
            "end_seconds": 180,
        },
    )
    assert missing_event_name.status_code == 422

    other_project = client.post("/api/v1/projects", json={"name": "Other project"}).json()
    cross_project = client.get(
        f"/api/v1/projects/{other_project['id']}/gaming/highlight-reviews/{review['id']}"
    )
    assert cross_project.status_code == 404

    listing = client.get(f"/api/v1/projects/{project['id']}/gaming/highlight-reviews")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [review["id"]]
