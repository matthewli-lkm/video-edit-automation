from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from video_edit_automation.domain.agent_workflow import HighlightAgentWorkflow
from video_edit_automation.domain.capture import CaptureSession
from video_edit_automation.domain.gaming import HighlightAnalysis
from video_edit_automation.domain.models import EditPlan, Job, MediaAsset, MediaProxy, Project
from video_edit_automation.domain.review import (
    HighlightHumanReviewState,
    HighlightReviewDecision,
    HighlightReviewSession,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class SQLiteRepository:
    """Small durable metadata store.

    Payloads stay as versionable Pydantic JSON while indexed columns support the access patterns the
    MVP needs. Domain-specific tables can replace this when query requirements become real.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    kind TEXT NOT NULL,
                    id TEXT NOT NULL,
                    project_id TEXT,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (kind, id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_entities_project
                ON entities (kind, project_id, created_at)
                """
            )

    def ping(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def _save(
        self,
        kind: str,
        entity: BaseModel,
        entity_id: UUID,
        project_id: UUID | None,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO entities (kind, id, project_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, id) DO UPDATE SET
                    project_id = excluded.project_id,
                    created_at = excluded.created_at,
                    payload = excluded.payload
                """,
                (
                    kind,
                    str(entity_id),
                    str(project_id) if project_id else None,
                    created_at,
                    entity.model_dump_json(),
                ),
            )

    def _get(self, kind: str, entity_id: UUID, parser: Callable[[str], ModelT]) -> ModelT | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM entities WHERE kind = ? AND id = ?",
                (kind, str(entity_id)),
            ).fetchone()
        return parser(row["payload"]) if row else None

    def _list(
        self,
        kind: str,
        parser: Callable[[str], ModelT],
        project_id: UUID | None = None,
    ) -> list[ModelT]:
        query = "SELECT payload FROM entities WHERE kind = ?"
        parameters: list[str] = [kind]
        if project_id is not None:
            query += " AND project_id = ?"
            parameters.append(str(project_id))
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [parser(row["payload"]) for row in rows]

    def save_project(self, project: Project) -> None:
        self._save("project", project, project.id, project.id, project.created_at.isoformat())

    def get_project(self, project_id: UUID) -> Project | None:
        return self._get("project", project_id, Project.model_validate_json)

    def list_projects(self) -> list[Project]:
        return self._list("project", Project.model_validate_json)

    def save_asset(self, asset: MediaAsset) -> None:
        self._save("asset", asset, asset.id, asset.project_id, asset.imported_at.isoformat())

    def get_asset(self, asset_id: UUID) -> MediaAsset | None:
        return self._get("asset", asset_id, MediaAsset.model_validate_json)

    def list_assets(self, project_id: UUID) -> list[MediaAsset]:
        return self._list("asset", MediaAsset.model_validate_json, project_id)

    def save_plan(self, plan: EditPlan) -> None:
        self._save("plan", plan, plan.id, plan.project_id, plan.created_at.isoformat())

    def get_plan(self, plan_id: UUID) -> EditPlan | None:
        return self._get("plan", plan_id, EditPlan.model_validate_json)

    def list_plans(self, project_id: UUID) -> list[EditPlan]:
        return self._list("plan", EditPlan.model_validate_json, project_id)

    def save_job(self, job: Job) -> None:
        self._save("job", job, job.id, job.project_id, job.created_at.isoformat())

    def get_job(self, job_id: UUID) -> Job | None:
        return self._get("job", job_id, Job.model_validate_json)

    def list_jobs(self, project_id: UUID) -> list[Job]:
        return self._list("job", Job.model_validate_json, project_id)

    def save_media_proxy(self, proxy: MediaProxy) -> None:
        self._save(
            "media_proxy",
            proxy,
            proxy.id,
            proxy.project_id,
            proxy.created_at.isoformat(),
        )

    def get_media_proxy(self, proxy_id: UUID) -> MediaProxy | None:
        return self._get("media_proxy", proxy_id, MediaProxy.model_validate_json)

    def list_media_proxies(self, project_id: UUID) -> list[MediaProxy]:
        return self._list("media_proxy", MediaProxy.model_validate_json, project_id)

    def save_capture_session(self, session: CaptureSession) -> None:
        self._save(
            "capture_session",
            session,
            session.id,
            session.project_id,
            session.created_at.isoformat(),
        )

    def get_capture_session(self, session_id: UUID) -> CaptureSession | None:
        return self._get("capture_session", session_id, CaptureSession.model_validate_json)

    def list_capture_sessions(self, project_id: UUID) -> list[CaptureSession]:
        return self._list(
            "capture_session",
            CaptureSession.model_validate_json,
            project_id,
        )

    def save_highlight_analysis(self, project_id: UUID, analysis: HighlightAnalysis) -> None:
        self._save(
            "highlight_analysis",
            analysis,
            analysis.id,
            project_id,
            analysis.created_at.isoformat(),
        )

    def get_highlight_analysis(self, analysis_id: UUID) -> HighlightAnalysis | None:
        return self._get(
            "highlight_analysis",
            analysis_id,
            HighlightAnalysis.model_validate_json,
        )

    def list_highlight_analyses(self, project_id: UUID) -> list[HighlightAnalysis]:
        return self._list(
            "highlight_analysis",
            HighlightAnalysis.model_validate_json,
            project_id,
        )

    def save_highlight_review_session(self, session: HighlightReviewSession) -> None:
        self._save(
            "highlight_review_session",
            session,
            session.id,
            session.project_id,
            session.created_at.isoformat(),
        )

    def get_highlight_review_session(
        self,
        session_id: UUID,
    ) -> HighlightReviewSession | None:
        return self._get(
            "highlight_review_session",
            session_id,
            HighlightReviewSession.model_validate_json,
        )

    def list_highlight_review_sessions(
        self,
        project_id: UUID,
    ) -> list[HighlightReviewSession]:
        return self._list(
            "highlight_review_session",
            HighlightReviewSession.model_validate_json,
            project_id,
        )

    def save_highlight_review_decision(self, decision: HighlightReviewDecision) -> None:
        self._save(
            "highlight_review_decision",
            decision,
            decision.id,
            decision.project_id,
            decision.created_at.isoformat(),
        )

    def list_highlight_review_decisions(
        self,
        session_id: UUID,
    ) -> list[HighlightReviewDecision]:
        decisions = self._list(
            "highlight_review_decision",
            HighlightReviewDecision.model_validate_json,
        )
        return [decision for decision in decisions if decision.review_session_id == session_id]

    def save_highlight_human_review_state(self, state: HighlightHumanReviewState) -> None:
        self._save(
            "highlight_human_review_state",
            state,
            state.review_session_id,
            state.project_id,
            state.created_at.isoformat(),
        )

    def get_highlight_human_review_state(
        self,
        session_id: UUID,
    ) -> HighlightHumanReviewState | None:
        return self._get(
            "highlight_human_review_state",
            session_id,
            HighlightHumanReviewState.model_validate_json,
        )

    def list_highlight_human_review_states(
        self,
        project_id: UUID,
    ) -> list[HighlightHumanReviewState]:
        return self._list(
            "highlight_human_review_state",
            HighlightHumanReviewState.model_validate_json,
            project_id,
        )

    def save_highlight_agent_workflow(self, workflow: HighlightAgentWorkflow) -> None:
        self._save(
            "highlight_agent_workflow",
            workflow,
            workflow.id,
            workflow.project_id,
            workflow.created_at.isoformat(),
        )

    def get_highlight_agent_workflow(
        self,
        workflow_id: UUID,
    ) -> HighlightAgentWorkflow | None:
        return self._get(
            "highlight_agent_workflow",
            workflow_id,
            HighlightAgentWorkflow.model_validate_json,
        )

    def list_highlight_agent_workflows(
        self,
        project_id: UUID,
    ) -> list[HighlightAgentWorkflow]:
        return self._list(
            "highlight_agent_workflow",
            HighlightAgentWorkflow.model_validate_json,
            project_id,
        )
