from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from video_edit_automation.application.ports import EditPlanner, MediaGateway, Repository
from video_edit_automation.domain.errors import (
    EntityNotFoundError,
    InvalidEditPlanError,
    MediaToolError,
    PathNotAllowedError,
    PlannerUnavailableError,
)
from video_edit_automation.domain.models import (
    AssetPlayback,
    AudioTrackRoleAssignment,
    EditBrief,
    EditPlan,
    EditPlanDraft,
    Job,
    JobStatus,
    MediaAsset,
    MediaProxy,
    PlanValidationReport,
    PlaybackMode,
    PlaybackStatus,
    Project,
    RenderPreset,
    RenderProfile,
    TranscriptSegment,
    ValidationIssue,
    ValidationSeverity,
    utc_now,
)
from video_edit_automation.infrastructure.paths import ImportPathPolicy, WorkspaceManager


class ProjectService:
    def __init__(
        self,
        repository: Repository,
        media: MediaGateway,
        path_policy: ImportPathPolicy,
        workspace: WorkspaceManager,
    ) -> None:
        self.repository = repository
        self.media = media
        self.path_policy = path_policy
        self.workspace = workspace

    def create(self, name: str) -> Project:
        project = Project(name=name)
        workspace_path = self.workspace.create_project(project.id, project.name)
        project = project.model_copy(update={"workspace_path": workspace_path})
        self.repository.save_project(project)
        return project

    def get(self, project_id: UUID) -> Project:
        project = self.repository.get_project(project_id)
        if project is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        return project

    def list(self) -> list[Project]:
        return self.repository.list_projects()

    def list_assets(self, project_id: UUID) -> list[MediaAsset]:
        self.get(project_id)
        return self.repository.list_assets(project_id)

    def get_asset(self, project_id: UUID, asset_id: UUID) -> MediaAsset:
        self.get(project_id)
        asset = self.repository.get_asset(asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {asset_id} was not found in project {project_id}"
            )
        return asset

    def resolve_asset_media(self, project_id: UUID, asset_id: UUID) -> Path:
        asset = self.get_asset(project_id, asset_id)
        try:
            return self.workspace.resolve_managed_import(project_id, asset.source_path)
        except PathNotAllowedError:
            return self.path_policy.resolve_media_file(asset.source_path)

    def import_asset(self, project_id: UUID, requested_path: str | Path) -> MediaAsset:
        self.get(project_id)
        source_path = self.path_policy.resolve_media_file(requested_path)
        return self._import_resolved_asset(project_id, source_path)

    def import_managed_asset(self, project_id: UUID, requested_path: Path) -> MediaAsset:
        self.get(project_id)
        source_path = self.workspace.resolve_managed_import(project_id, requested_path)
        return self._import_resolved_asset(project_id, source_path)

    def _import_resolved_asset(self, project_id: UUID, source_path: Path) -> MediaAsset:
        stat = source_path.stat()
        fingerprint_source = f"{source_path}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
        fingerprint = hashlib.sha256(fingerprint_source).hexdigest()
        existing = next(
            (
                asset
                for asset in self.repository.list_assets(project_id)
                if asset.source_fingerprint == fingerprint
            ),
            None,
        )
        if existing:
            return existing

        probed = self.media.probe(source_path)
        asset = MediaAsset(
            **probed.model_dump(),
            project_id=project_id,
            source_path=source_path,
            source_fingerprint=fingerprint,
            size_bytes=stat.st_size,
            modified_at_ns=stat.st_mtime_ns,
        )
        self.repository.save_asset(asset)
        return asset

    def assign_audio_track_roles(
        self,
        project_id: UUID,
        asset_id: UUID,
        assignments: list[AudioTrackRoleAssignment],
    ) -> MediaAsset:
        self.get(project_id)
        asset = self.repository.get_asset(asset_id)
        if asset is None or asset.project_id != project_id:
            raise EntityNotFoundError(
                f"Media asset {asset_id} was not found in project {project_id}"
            )
        if not asset.audio_tracks:
            raise MediaToolError(
                "This asset has no individually addressable audio tracks; "
                "re-import it after probing"
            )
        assignment_by_index = {item.stream_index: item.role for item in assignments}
        if len(assignment_by_index) != len(assignments):
            raise MediaToolError("Each audio stream may be assigned only once per request")
        known_indexes = {track.stream_index for track in asset.audio_tracks}
        unknown_indexes = set(assignment_by_index) - known_indexes
        if unknown_indexes:
            indexes = ", ".join(str(index) for index in sorted(unknown_indexes))
            raise MediaToolError(f"Unknown audio stream index(es): {indexes}")

        updated = asset.model_copy(
            update={
                "audio_tracks": [
                    track.model_copy(
                        update={"role": assignment_by_index.get(track.stream_index, track.role)}
                    )
                    for track in asset.audio_tracks
                ]
            }
        )
        self.repository.save_asset(updated)
        return updated


@dataclass(frozen=True, slots=True)
class ProxyQueueResult:
    playback: AssetPlayback
    should_run: bool
    proxy_id: UUID | None = None


class MediaProxyService:
    def __init__(
        self,
        repository: Repository,
        media: MediaGateway,
        projects: ProjectService,
        workspace: WorkspaceManager,
        maximum_width: int,
    ) -> None:
        self.repository = repository
        self.media = media
        self.projects = projects
        self.workspace = workspace
        self.maximum_width = maximum_width
        self._queue_lock = Lock()

    @staticmethod
    def _requires_proxy(asset: MediaAsset) -> bool:
        return not (
            asset.source_path.suffix.lower() == ".mp4"
            and asset.video_codec in {"h264", "avc1"}
            and (not asset.has_audio or asset.audio_codec in {"aac", "mp3"})
        )

    def _matching_proxy(self, asset: MediaAsset) -> MediaProxy | None:
        return next(
            (
                proxy
                for proxy in self.repository.list_media_proxies(asset.project_id)
                if proxy.asset_id == asset.id
                and proxy.source_fingerprint == asset.source_fingerprint
                and proxy.maximum_width == self.maximum_width
            ),
            None,
        )

    @staticmethod
    def _playback_status(proxy: MediaProxy) -> PlaybackStatus:
        return {
            JobStatus.QUEUED: PlaybackStatus.QUEUED,
            JobStatus.RUNNING: PlaybackStatus.RUNNING,
            JobStatus.SUCCEEDED: PlaybackStatus.READY,
            JobStatus.FAILED: PlaybackStatus.FAILED,
        }[proxy.status]

    def get(self, project_id: UUID, asset_id: UUID) -> AssetPlayback:
        asset = self.projects.get_asset(project_id, asset_id)
        if not self._requires_proxy(asset):
            return AssetPlayback(
                asset_id=asset.id,
                mode=PlaybackMode.ORIGINAL,
                status=PlaybackStatus.READY,
                progress=1,
            )
        proxy = self._matching_proxy(asset)
        if proxy is None:
            return AssetPlayback(
                asset_id=asset.id,
                mode=PlaybackMode.PROXY,
                status=PlaybackStatus.NOT_STARTED,
            )
        if proxy.status == JobStatus.SUCCEEDED:
            try:
                self.workspace.resolve_proxy_output(project_id, proxy.output_path)
            except PathNotAllowedError as exc:
                proxy = proxy.model_copy(
                    update={
                        "status": JobStatus.FAILED,
                        "error": str(exc),
                        "finished_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                self.repository.save_media_proxy(proxy)
        return AssetPlayback(
            asset_id=asset.id,
            mode=PlaybackMode.PROXY,
            status=self._playback_status(proxy),
            proxy_id=proxy.id,
            progress=proxy.progress,
            error=proxy.error,
            updated_at=proxy.updated_at,
        )

    def prepare(self, project_id: UUID, asset_id: UUID) -> ProxyQueueResult:
        with self._queue_lock:
            asset = self.projects.get_asset(project_id, asset_id)
            current = self.get(project_id, asset_id)
            if current.mode == PlaybackMode.ORIGINAL or current.status in {
                PlaybackStatus.QUEUED,
                PlaybackStatus.RUNNING,
                PlaybackStatus.READY,
            }:
                return ProxyQueueResult(playback=current, should_run=False)

            proxy = (
                self.repository.get_media_proxy(current.proxy_id)
                if current.proxy_id is not None
                else None
            )
            if proxy is None:
                proxy_id = uuid5(
                    NAMESPACE_URL,
                    (
                        "vea:browser-proxy-v1:"
                        f"{asset.id}:{asset.source_fingerprint}:{self.maximum_width}"
                    ),
                )
                proxy = MediaProxy(
                    id=proxy_id,
                    project_id=project_id,
                    asset_id=asset.id,
                    source_fingerprint=asset.source_fingerprint,
                    maximum_width=self.maximum_width,
                    output_path=self.workspace.proxy_output(project_id, proxy_id),
                )
            else:
                proxy = proxy.model_copy(
                    update={
                        "status": JobStatus.QUEUED,
                        "progress": 0,
                        "error": None,
                        "started_at": None,
                        "finished_at": None,
                        "updated_at": utc_now(),
                    }
                )
            self.repository.save_media_proxy(proxy)
            return ProxyQueueResult(
                playback=AssetPlayback(
                    asset_id=asset.id,
                    mode=PlaybackMode.PROXY,
                    status=PlaybackStatus.QUEUED,
                    proxy_id=proxy.id,
                    progress=proxy.progress,
                    updated_at=proxy.updated_at,
                ),
                should_run=True,
                proxy_id=proxy.id,
            )

    def run(self, proxy_id: UUID) -> None:
        proxy = self.repository.get_media_proxy(proxy_id)
        if proxy is None:
            raise EntityNotFoundError(f"Media proxy {proxy_id} was not found")
        if proxy.status != JobStatus.QUEUED:
            return
        running = proxy.model_copy(
            update={
                "status": JobStatus.RUNNING,
                "progress": 0.05,
                "started_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        self.repository.save_media_proxy(running)
        try:
            asset = self.projects.get_asset(running.project_id, running.asset_id)
            if asset.source_fingerprint != running.source_fingerprint:
                raise MediaToolError(
                    "The registered source changed after this browser preview was queued"
                )
            source_path = self.projects.resolve_asset_media(
                running.project_id,
                running.asset_id,
            )
            source_stat = source_path.stat()
            if (
                source_stat.st_size != asset.size_bytes
                or source_stat.st_mtime_ns != asset.modified_at_ns
            ):
                raise MediaToolError(
                    "The source recording changed after import; re-import it before "
                    "preparing browser playback"
                )
            self.media.create_proxy(
                asset.model_copy(update={"source_path": source_path}),
                running.output_path,
                running.maximum_width,
            )
            self.workspace.resolve_proxy_output(running.project_id, running.output_path)
        except Exception as exc:  # Background tasks must persist a terminal state.
            failed = running.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "error": str(exc)[:4_000],
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            self.repository.save_media_proxy(failed)
            return
        succeeded = running.model_copy(
            update={
                "status": JobStatus.SUCCEEDED,
                "progress": 1,
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        self.repository.save_media_proxy(succeeded)

    def resolve_media(self, project_id: UUID, asset_id: UUID) -> Path:
        playback = self.get(project_id, asset_id)
        if playback.mode == PlaybackMode.ORIGINAL:
            return self.projects.resolve_asset_media(project_id, asset_id)
        if (
            playback.status != PlaybackStatus.READY
            or playback.proxy_id is None
        ):
            raise EntityNotFoundError(
                f"Browser playback for asset {asset_id} is not ready"
            )
        proxy = self.repository.get_media_proxy(playback.proxy_id)
        if proxy is None:
            raise EntityNotFoundError(
                f"Browser preview proxy {playback.proxy_id} was not found"
            )
        return self.workspace.resolve_proxy_output(project_id, proxy.output_path)

    def recover_interrupted(self) -> int:
        recovered = 0
        for project in self.repository.list_projects():
            for proxy in self.repository.list_media_proxies(project.id):
                if proxy.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    continue
                self.repository.save_media_proxy(
                    proxy.model_copy(
                        update={
                            "status": JobStatus.FAILED,
                            "error": (
                                "Browser preview was interrupted when the application stopped. "
                                "Retry preparation to continue."
                            ),
                            "finished_at": utc_now(),
                            "updated_at": utc_now(),
                        }
                    )
                )
                recovered += 1
        return recovered


class PlanValidator:
    def __init__(
        self,
        minimum_segment_seconds: float = 0.2,
        tolerance_seconds: float = 0.05,
    ) -> None:
        self.minimum_segment_seconds = minimum_segment_seconds
        self.tolerance_seconds = tolerance_seconds

    def validate(
        self,
        project_id: UUID,
        draft: EditPlanDraft,
        assets: list[MediaAsset],
        brief: EditBrief,
    ) -> PlanValidationReport:
        assets_by_id = {asset.id: asset for asset in assets}
        issues: list[ValidationIssue] = []

        for index, segment in enumerate(draft.segments):
            asset = assets_by_id.get(segment.asset_id)
            if asset is None or asset.project_id != project_id:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="unknown_asset",
                        message=f"Asset {segment.asset_id} is not part of this project",
                        segment_index=index,
                    )
                )
                continue
            if segment.duration_seconds < self.minimum_segment_seconds:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="segment_too_short",
                        message=(
                            f"Segment duration {segment.duration_seconds:.3f}s is below the "
                            f"{self.minimum_segment_seconds:.3f}s minimum"
                        ),
                        segment_index=index,
                    )
                )
            if segment.source_out_seconds > asset.duration_seconds + self.tolerance_seconds:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="out_of_bounds",
                        message=(
                            f"Segment ends at {segment.source_out_seconds:.3f}s but asset duration "
                            f"is {asset.duration_seconds:.3f}s"
                        ),
                        segment_index=index,
                    )
                )
            if not asset.has_video:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="missing_video",
                        message="The current renderer requires a video stream",
                        segment_index=index,
                    )
                )
            if not asset.has_audio:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="missing_audio",
                        message="The current renderer requires an audio stream",
                        segment_index=index,
                    )
                )

        total_duration = draft.duration_seconds
        target = brief.target_duration_seconds
        if target is not None:
            allowed_difference = max(2.0, target * 0.10)
            if abs(total_duration - target) > allowed_difference:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="target_duration_mismatch",
                        message=(
                            f"Plan duration is {total_duration:.1f}s versus a {target:.1f}s target"
                        ),
                    )
                )

        return PlanValidationReport(
            valid=not any(issue.severity == ValidationSeverity.ERROR for issue in issues),
            total_duration_seconds=total_duration,
            issues=issues,
        )


class PlanService:
    def __init__(
        self,
        repository: Repository,
        validator: PlanValidator,
        planner: EditPlanner | None = None,
    ) -> None:
        self.repository = repository
        self.validator = validator
        self.planner = planner

    def _require_project(self, project_id: UUID) -> None:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")

    def _save_valid_plan(
        self,
        project_id: UUID,
        brief: EditBrief,
        draft: EditPlanDraft,
        generated_by: str,
    ) -> tuple[EditPlan, PlanValidationReport]:
        self._require_project(project_id)
        assets = self.repository.list_assets(project_id)
        report = self.validator.validate(project_id, draft, assets, brief)
        if not report.valid:
            details = "; ".join(
                issue.message
                for issue in report.issues
                if issue.severity == ValidationSeverity.ERROR
            )
            raise InvalidEditPlanError(details)

        previous = self.repository.list_plans(project_id)
        version = max((plan.version for plan in previous), default=0) + 1
        plan = EditPlan(
            **draft.model_dump(),
            project_id=project_id,
            version=version,
            brief=brief,
            generated_by=generated_by,
        )
        self.repository.save_plan(plan)
        return plan, report

    def create_manual(
        self,
        project_id: UUID,
        brief: EditBrief,
        draft: EditPlanDraft,
    ) -> tuple[EditPlan, PlanValidationReport]:
        return self._save_valid_plan(project_id, brief, draft, "manual")

    def create_derived(
        self,
        project_id: UUID,
        brief: EditBrief,
        draft: EditPlanDraft,
        generated_by: str,
    ) -> tuple[EditPlan, PlanValidationReport]:
        return self._save_valid_plan(project_id, brief, draft, generated_by)

    def generate(
        self,
        project_id: UUID,
        brief: EditBrief,
        transcript: list[TranscriptSegment],
    ) -> tuple[EditPlan, PlanValidationReport]:
        self._require_project(project_id)
        if self.planner is None:
            raise PlannerUnavailableError(
                "No local planner is configured; set VEA_LLM_MODEL or create a manual plan"
            )
        assets = self.repository.list_assets(project_id)
        draft = self.planner.create_draft(brief, assets, transcript)
        return self._save_valid_plan(project_id, brief, draft, self.planner.name)

    def get(self, project_id: UUID, plan_id: UUID) -> EditPlan:
        plan = self.repository.get_plan(plan_id)
        if plan is None or plan.project_id != project_id:
            raise EntityNotFoundError(f"Edit plan {plan_id} was not found in project {project_id}")
        return plan

    def list(self, project_id: UUID) -> list[EditPlan]:
        self._require_project(project_id)
        return self.repository.list_plans(project_id)

    def validate_saved(self, project_id: UUID, plan_id: UUID) -> PlanValidationReport:
        plan = self.get(project_id, plan_id)
        return self.validator.validate(
            project_id,
            EditPlanDraft(**plan.model_dump(include={"title", "summary", "segments"})),
            self.repository.list_assets(project_id),
            plan.brief,
        )


class RenderService:
    def __init__(
        self,
        repository: Repository,
        media: MediaGateway,
        validator: PlanValidator,
        workspace: WorkspaceManager,
    ) -> None:
        self.repository = repository
        self.media = media
        self.validator = validator
        self.workspace = workspace
        self._queue_lock = Lock()

    def _load_render_context(
        self,
        project_id: UUID,
        plan_id: UUID,
    ) -> tuple[EditPlan, dict[UUID, MediaAsset]]:
        plan = self.repository.get_plan(plan_id)
        if plan is None or plan.project_id != project_id:
            raise EntityNotFoundError(f"Edit plan {plan_id} was not found in project {project_id}")
        assets = self.repository.list_assets(project_id)
        draft = EditPlanDraft(**plan.model_dump(include={"title", "summary", "segments"}))
        report = self.validator.validate(project_id, draft, assets, plan.brief)
        if not report.valid:
            raise InvalidEditPlanError(
                "The saved edit plan is no longer valid for its source assets"
            )
        return plan, {asset.id: asset for asset in assets}

    def dry_run(
        self,
        project_id: UUID,
        plan_id: UUID,
        preset: RenderPreset,
    ) -> list[str]:
        plan, assets = self._load_render_context(project_id, plan_id)
        placeholder = self.workspace.render_output(project_id, uuid4(), preset.profile.value)
        return self.media.build_render_command(plan, assets, placeholder, preset)

    def queue(self, project_id: UUID, plan_id: UUID, preset: RenderPreset) -> tuple[Job, bool]:
        with self._queue_lock:
            return self._queue_unlocked(project_id, plan_id, preset)

    def _queue_unlocked(
        self,
        project_id: UUID,
        plan_id: UUID,
        preset: RenderPreset,
    ) -> tuple[Job, bool]:
        plan, _assets = self._load_render_context(project_id, plan_id)
        if preset.profile == RenderProfile.FINAL:
            manually_trimmed = plan.generated_by == "manual-workflow"
            approved = manually_trimmed or any(
                state.current_plan_id == plan.id
                and any(
                    approval.id == state.active_approval_id
                    and approval.plan_id == plan.id
                    and approval.plan_version == plan.version
                    for approval in state.approvals
                )
                for state in self.repository.list_highlight_human_review_states(project_id)
            )
            if not approved:
                raise InvalidEditPlanError(
                    "Final rendering requires explicit human approval of this exact plan version"
                )
        for existing in self.repository.list_jobs(project_id):
            if existing.plan_id != plan_id or existing.preset != preset:
                continue
            if existing.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                return existing, False
            if existing.status == JobStatus.SUCCEEDED and existing.output_path is not None:
                try:
                    self.workspace.resolve_render_output(project_id, existing.output_path)
                except PathNotAllowedError:
                    continue
                return existing, False
        job_id = uuid5(
            NAMESPACE_URL,
            f"vea:render-v1:{project_id}:{plan_id}:{preset.model_dump_json()}",
        )
        job = Job(id=job_id, project_id=project_id, plan_id=plan_id, preset=preset)
        job = job.model_copy(
            update={
                "output_path": self.workspace.render_output(
                    project_id,
                    job.id,
                    preset.profile.value,
                    filename=(
                        plan.segments[0].purpose
                        if preset.profile == RenderProfile.FINAL
                        and len(plan.segments) == 1
                        and plan.segments[0].purpose
                        else f"{plan.title} highlight reel"
                    ),
                )
            }
        )
        self.repository.save_job(job)
        return job, True

    def run(self, job_id: UUID) -> None:
        job = self.get_job(job_id)
        if job.status != JobStatus.QUEUED:
            return
        running = job.model_copy(
            update={
                "status": JobStatus.RUNNING,
                "progress": 0.05,
                "started_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        self.repository.save_job(running)
        try:
            plan, assets = self._load_render_context(running.project_id, running.plan_id)
            if running.output_path is None:
                raise InvalidEditPlanError("Render job has no output path")
            self.media.render(plan, assets, running.output_path, running.preset)
        except Exception as exc:  # Background tasks must persist a terminal state.
            failed = running.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "error": str(exc)[:4_000],
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            self.repository.save_job(failed)
            return

        succeeded = running.model_copy(
            update={
                "status": JobStatus.SUCCEEDED,
                "progress": 1.0,
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        self.repository.save_job(succeeded)

    def get_job(self, job_id: UUID) -> Job:
        job = self.repository.get_job(job_id)
        if job is None:
            raise EntityNotFoundError(f"Job {job_id} was not found")
        return job

    def list(self, project_id: UUID) -> list[Job]:
        if self.repository.get_project(project_id) is None:
            raise EntityNotFoundError(f"Project {project_id} was not found")
        return self.repository.list_jobs(project_id)

    def recover_interrupted(self) -> int:
        recovered = 0
        for project in self.repository.list_projects():
            for job in self.repository.list_jobs(project.id):
                if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    continue
                self.repository.save_job(
                    job.model_copy(
                        update={
                            "status": JobStatus.FAILED,
                            "error": (
                                "Render was interrupted when the application stopped. "
                                "Request the render again to retry."
                            ),
                            "finished_at": utc_now(),
                            "updated_at": utc_now(),
                        }
                    )
                )
                recovered += 1
        return recovered
