from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _create_project_and_asset(
    client: TestClient,
    media_root: Path,
) -> tuple[dict, dict]:
    project_response = client.post("/api/v1/projects", json={"name": "Lecture highlights"})
    assert project_response.status_code == 201
    project = project_response.json()

    source = media_root / "lecture.mov"
    source.write_bytes(b"fake source")
    asset_response = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    )
    assert asset_response.status_code == 201
    return project, asset_response.json()


def test_foundation_flow(client: TestClient, media_root: Path) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    project, asset = _create_project_and_asset(client, media_root)
    plan_response = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans",
        json={
            "brief": {
                "objective": "Create a concise explanation",
                "target_duration_seconds": 3,
                "aspect_ratio": "16:9",
            },
            "draft": {
                "title": "Key explanation",
                "summary": "The clearest short explanation from the lecture.",
                "segments": [
                    {
                        "asset_id": asset["id"],
                        "source_in_seconds": 1,
                        "source_out_seconds": 4,
                        "purpose": "Main explanation",
                    }
                ],
            },
        },
    )
    assert plan_response.status_code == 201
    plan_payload = plan_response.json()
    assert plan_payload["validation"]["valid"] is True
    plan_id = plan_payload["plan"]["id"]

    dry_run = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans/{plan_id}/renders/dry-run",
        json={"preset": {"profile": "preview", "aspect_ratio": "16:9"}},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["argv"][0] == "ffmpeg"

    queued = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans/{plan_id}/renders",
        json={"preset": {"profile": "preview", "aspect_ratio": "16:9"}},
    )
    assert queued.status_code == 202
    job = client.get(f"/api/v1/jobs/{queued.json()['id']}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert Path(job.json()["output_path"]).is_file()


def test_import_is_idempotent(client: TestClient, media_root: Path) -> None:
    project, first = _create_project_and_asset(client, media_root)
    second = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(media_root / "lecture.mov")},
    )
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]


def test_source_media_is_project_scoped(client: TestClient, media_root: Path) -> None:
    project, asset = _create_project_and_asset(client, media_root)
    media = client.get(f"/api/v1/projects/{project['id']}/assets/{asset['id']}/media")
    assert media.status_code == 200
    assert media.content == b"fake source"

    other_project = client.post("/api/v1/projects", json={"name": "Other project"}).json()
    blocked = client.get(f"/api/v1/projects/{other_project['id']}/assets/{asset['id']}/media")
    assert blocked.status_code == 404


def test_import_outside_allowed_root_is_rejected(client: TestClient, tmp_path: Path) -> None:
    project = client.post("/api/v1/projects", json={"name": "Restricted"}).json()
    outside = tmp_path / "outside.mov"
    outside.write_bytes(b"not allowed")
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(outside)},
    )
    assert response.status_code == 403
    assert response.json()["error_type"] == "PathNotAllowedError"


def test_model_planning_is_explicitly_disabled(client: TestClient, media_root: Path) -> None:
    project, _asset = _create_project_and_asset(client, media_root)
    response = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans/generate",
        json={
            "brief": {"objective": "Create a short summary"},
            "transcript": [{"start_seconds": 0, "end_seconds": 1, "text": "A useful sentence."}],
        },
    )
    assert response.status_code == 503
    assert response.json()["error_type"] == "PlannerUnavailableError"


def test_out_of_bounds_plan_is_rejected(client: TestClient, media_root: Path) -> None:
    project, asset = _create_project_and_asset(client, media_root)
    response = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans",
        json={
            "brief": {"objective": "Make a clip"},
            "draft": {
                "title": "Invalid",
                "summary": "This range exceeds the source.",
                "segments": [
                    {
                        "asset_id": asset["id"],
                        "source_in_seconds": 9,
                        "source_out_seconds": 20,
                    }
                ],
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error_type"] == "InvalidEditPlanError"
