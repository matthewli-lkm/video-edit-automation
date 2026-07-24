from __future__ import annotations

from pathlib import Path
from uuid import UUID

from conftest import FakeMediaGateway
from fastapi.testclient import TestClient

from video_edit_automation.config import Settings
from video_edit_automation.domain.models import (
    Job,
    JobStatus,
    RenderPreset,
    RenderProfile,
)
from video_edit_automation.main import create_app


def _project(client: TestClient, name: str = "Reliable playback") -> dict:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _asset(client: TestClient, media_root: Path, project: dict, filename: str) -> dict:
    source = media_root / filename
    source.write_bytes(b"original source remains unchanged")
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets/import",
        json={"local_path": str(source)},
    )
    assert response.status_code == 201
    return response.json()


def _plan(client: TestClient, project: dict, asset: dict) -> dict:
    response = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans",
        json={
            "brief": {"objective": "Create a stable preview"},
            "draft": {
                "title": "Stable preview",
                "summary": "A short deterministic preview.",
                "segments": [
                    {
                        "asset_id": asset["id"],
                        "source_in_seconds": 1,
                        "source_out_seconds": 4,
                    }
                ],
            },
        },
    )
    assert response.status_code == 201
    return response.json()["plan"]


def test_incompatible_source_gets_one_managed_seekable_proxy(
    client: TestClient,
    media_root: Path,
) -> None:
    project = _project(client)
    asset = _asset(client, media_root, project, "比賽 recording.mkv")
    source = media_root / "比賽 recording.mkv"
    checksum_before = source.read_bytes()

    initial = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback"
    )
    assert initial.status_code == 200
    assert initial.json()["mode"] == "proxy"
    assert initial.json()["status"] == "not_started"

    prepared = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/prepare"
    )
    assert prepared.status_code == 202
    proxy_id = prepared.json()["proxy_id"]

    repeated = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/prepare"
    )
    assert repeated.status_code == 202
    assert repeated.json()["proxy_id"] == proxy_id

    state = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback"
    )
    assert state.json()["status"] == "ready"
    assert len(client.app.state.container.repository.list_media_proxies(project["id"])) == 1

    media = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/media",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Range": "bytes=0-3",
        },
    )
    assert media.status_code == 206
    assert media.headers["accept-ranges"] == "bytes"
    assert media.headers["content-range"].startswith("bytes 0-3/")
    assert "content-range" in media.headers["access-control-expose-headers"].lower()
    assert media.content == b"fake"
    assert source.read_bytes() == checksum_before


def test_compatible_mp4_uses_the_original_without_creating_proxy(
    client: TestClient,
    media_root: Path,
) -> None:
    project = _project(client)
    asset = _asset(client, media_root, project, "browser ready.mp4")

    prepared = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/prepare"
    )
    assert prepared.status_code == 202
    assert prepared.json()["mode"] == "original"
    assert prepared.json()["status"] == "ready"
    assert prepared.json()["proxy_id"] is None
    assert client.app.state.container.repository.list_media_proxies(project["id"]) == []

    media = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/media"
    )
    assert media.status_code == 200
    assert media.content == b"original source remains unchanged"


def test_proxy_reuse_includes_settings_and_preserves_unusual_paths(
    settings: Settings,
    media_root: Path,
) -> None:
    nested = media_root / ("錄影 " + "很長的資料夾名稱" * 8)
    nested.mkdir()
    source = nested / ("比賽 " + "片段" * 30 + ".mkv")
    source.write_bytes(b"original source remains unchanged")

    with TestClient(create_app(settings=settings, media=FakeMediaGateway())) as first:
        project = _project(first, "Proxy settings")
        imported = first.post(
            f"/api/v1/projects/{project['id']}/assets/import",
            json={"local_path": str(source)},
        ).json()
        prepared = first.post(
            f"/api/v1/projects/{project['id']}/assets/{imported['id']}/playback/prepare"
        )
        assert prepared.status_code == 202
        first_proxy_id = prepared.json()["proxy_id"]

    narrower = settings.model_copy(update={"proxy_maximum_width": 640})
    with TestClient(create_app(settings=narrower, media=FakeMediaGateway())) as second:
        prepared = second.post(
            f"/api/v1/projects/{project['id']}/assets/{imported['id']}/playback/prepare"
        )
        assert prepared.status_code == 202
        assert prepared.json()["proxy_id"] != first_proxy_id
        proxies = second.app.state.container.repository.list_media_proxies(
            UUID(project["id"])
        )
        assert {proxy.maximum_width for proxy in proxies} == {640, 1280}

    assert source.read_bytes() == b"original source remains unchanged"


def test_proxy_fails_visibly_when_source_changed_after_import(
    client: TestClient,
    media_root: Path,
) -> None:
    project = _project(client, "Changed source")
    asset = _asset(client, media_root, project, "changed after import.mkv")
    source = media_root / "changed after import.mkv"
    source.write_bytes(b"changed source contents")

    queued = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/prepare"
    )
    assert queued.status_code == 202
    state = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback"
    ).json()
    assert state["status"] == "failed"
    assert "changed after import" in state["error"]


def test_asset_playback_is_project_scoped(
    client: TestClient,
    media_root: Path,
) -> None:
    owner = _project(client, "Owner")
    other = _project(client, "Other")
    asset = _asset(client, media_root, owner, "private source.mkv")

    response = client.get(
        f"/api/v1/projects/{other['id']}/assets/{asset['id']}/playback"
    )
    assert response.status_code == 404


def test_proxy_media_route_rejects_paths_outside_managed_workspace(
    client: TestClient,
    media_root: Path,
) -> None:
    project = _project(client)
    asset = _asset(client, media_root, project, "unsafe proxy source.mkv")
    prepared = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/prepare"
    ).json()
    proxy = client.app.state.container.repository.get_media_proxy(
        UUID(prepared["proxy_id"])
    )
    assert proxy is not None
    outside = media_root / "outside.mp4"
    outside.write_bytes(b"not managed")
    client.app.state.container.repository.save_media_proxy(
        proxy.model_copy(update={"output_path": outside})
    )

    state = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback"
    )
    assert state.status_code == 200
    assert state.json()["status"] == "failed"
    assert "project workspace" in state.json()["error"]
    blocked = client.get(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/media"
    )
    assert blocked.status_code == 404


def test_render_requests_are_deduplicated_and_listed_by_project(
    client: TestClient,
    media_root: Path,
) -> None:
    project = _project(client)
    asset = _asset(client, media_root, project, "render source.mp4")
    plan = _plan(client, project, asset)
    payload = {
        "preset": {
            "profile": "preview",
            "aspect_ratio": "16:9",
            "frames_per_second": 30,
        }
    }

    first = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans/{plan['id']}/renders",
        json=payload,
    )
    second = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans/{plan['id']}/renders",
        json=payload,
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]

    changed = client.post(
        f"/api/v1/projects/{project['id']}/edit-plans/{plan['id']}/renders",
        json={
            "preset": {
                "profile": "preview",
                "aspect_ratio": "16:9",
                "frames_per_second": 60,
            }
        },
    )
    assert changed.status_code == 202
    assert changed.json()["id"] != first.json()["id"]

    jobs = client.get(f"/api/v1/projects/{project['id']}/jobs")
    assert jobs.status_code == 200
    assert {job["id"] for job in jobs.json()} == {
        first.json()["id"],
        changed.json()["id"],
    }
    assert all(job["status"] == "succeeded" for job in jobs.json())


def test_final_download_is_an_attachment_and_preview_download_is_rejected(
    client: TestClient,
    media_root: Path,
) -> None:
    project = _project(client, "Download")
    asset = _asset(client, media_root, project, "download source.mp4")
    plan = _plan(client, project, asset)
    project_id = UUID(project["id"])
    plan_id = UUID(plan["id"])
    repository = client.app.state.container.repository
    workspace = client.app.state.container.workspace

    final = Job(
        project_id=project_id,
        plan_id=plan_id,
        status=JobStatus.SUCCEEDED,
        preset=RenderPreset(profile=RenderProfile.FINAL),
    )
    final_output = workspace.render_output(project_id, final.id, "final")
    final_output.write_bytes(b"final video")
    repository.save_job(final.model_copy(update={"output_path": final_output}))

    downloaded = client.get(f"/api/v1/jobs/{final.id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"final video"
    assert downloaded.headers["content-disposition"].startswith("attachment;")

    preview = final.model_copy(
        update={
            "id": UUID("00000000-0000-0000-0000-000000000123"),
            "preset": RenderPreset(profile=RenderProfile.PREVIEW),
        }
    )
    repository.save_job(preview)
    assert client.get(f"/api/v1/jobs/{preview.id}/download").status_code == 404


def test_range_requests_are_allowed_from_dashboard_origin(
    client: TestClient,
) -> None:
    response = client.options(
        "/api/v1/projects/00000000-0000-0000-0000-000000000001/assets/"
        "00000000-0000-0000-0000-000000000002/playback/media",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Range",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "range" in response.headers["access-control-allow-headers"].lower()


def test_restart_marks_interrupted_proxy_and_render_as_recoverable(
    settings: Settings,
    media_root: Path,
) -> None:
    with TestClient(create_app(settings=settings, media=FakeMediaGateway())) as first:
        project = _project(first, "Restart recovery")
        asset = _asset(first, media_root, project, "restart source.mkv")
        proxy = first.app.state.container.proxies.prepare(
            UUID(project["id"]),
            UUID(asset["id"]),
        ).playback
        assert proxy.proxy_id is not None
        stored_proxy = first.app.state.container.repository.get_media_proxy(proxy.proxy_id)
        assert stored_proxy is not None
        first.app.state.container.repository.save_media_proxy(
            stored_proxy.model_copy(update={"status": JobStatus.RUNNING})
        )

        plan = _plan(first, project, asset)
        job, created = first.app.state.container.renders.queue(
            UUID(project["id"]),
            UUID(plan["id"]),
            RenderPreset(),
        )
        assert created is True

    with TestClient(create_app(settings=settings, media=FakeMediaGateway())) as restarted:
        proxy_state = restarted.get(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback"
        )
        assert proxy_state.status_code == 200
        assert proxy_state.json()["status"] == "failed"
        assert "interrupted" in proxy_state.json()["error"].lower()

        recovered_job = restarted.get(f"/api/v1/jobs/{job.id}")
        assert recovered_job.status_code == 200
        assert recovered_job.json()["status"] == "failed"
        assert "interrupted" in recovered_job.json()["error"].lower()

        retried = restarted.post(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback/prepare"
        )
        assert retried.status_code == 202
        assert retried.json()["proxy_id"] == str(proxy.proxy_id)
        assert restarted.get(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}/playback"
        ).json()["status"] == "ready"
