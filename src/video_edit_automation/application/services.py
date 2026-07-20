from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from video_edit_automation.application.ports import EditPlanner, MediaGateway, Repository
from video_edit_automation.domain.errors import (
    EntityNotFoundError,
    InvalidEditPlanError,
    PlannerUnavailableError,
)
from video_edit_automation.domain.models import (
    EditBrief,
    EditPlan,
    EditPlanDraft,
    Job,
    JobStatus,
    MediaAsset,
    PlanValidationReport,
    Project,
    RenderPreset,
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
        self.workspace.ensure_project(project.id)
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

    def import_asset(self, project_id: UUID, requested_path: str | Path) -> MediaAsset:
        self.get(project_id)
        source_path = self.path_policy.resolve_media_file(requested_path)
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

    def queue(self, project_id: UUID, plan_id: UUID, preset: RenderPreset) -> Job:
        self._load_render_context(project_id, plan_id)
        job = Job(project_id=project_id, plan_id=plan_id, preset=preset)
        job = job.model_copy(
            update={
                "output_path": self.workspace.render_output(
                    project_id,
                    job.id,
                    preset.profile.value,
                )
            }
        )
        self.repository.save_job(job)
        return job

    def run(self, job_id: UUID) -> None:
        job = self.get_job(job_id)
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
