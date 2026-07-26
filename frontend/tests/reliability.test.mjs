import assert from "node:assert/strict";
import test from "node:test";

import {
  playbackStatusCopy,
  restoreAssetId,
  restoreRenderJobs,
  restoreWorkflow,
} from "../app/reliability.ts";

test("restores the latest preview and final jobs for the current plan", () => {
  const jobs = [
    {
      id: "other-plan",
      plan_id: "plan-1",
      status: "succeeded",
      preset: { profile: "final" },
      created_at: "2026-07-24T09:00:00Z",
    },
    {
      id: "preview-ready",
      plan_id: "plan-2",
      status: "succeeded",
      preset: { profile: "preview" },
      created_at: "2026-07-24T09:30:00Z",
    },
    {
      id: "final-failed",
      plan_id: "plan-2",
      status: "failed",
      preset: { profile: "final" },
      created_at: "2026-07-24T09:45:00Z",
    },
  ];

  assert.deepEqual(restoreRenderJobs(jobs, "plan-2"), {
    preview: jobs[1],
    final: jobs[2],
  });
});

test("restores an active preview so polling resumes after refresh", () => {
  const jobs = [
    {
      id: "preview-running",
      plan_id: "plan-2",
      status: "running",
      preset: { profile: "preview" },
      created_at: "2026-07-24T09:50:00Z",
    },
    {
      id: "preview-ready",
      plan_id: "plan-2",
      status: "succeeded",
      preset: { profile: "preview" },
      created_at: "2026-07-24T09:30:00Z",
    },
  ];

  assert.equal(restoreRenderJobs(jobs, "plan-2").preview?.id, "preview-running");
});

test("restores the exact recording when it still belongs to the project", () => {
  assert.equal(restoreAssetId(["asset-new", "asset-old"], "asset-old"), "asset-old");
  assert.equal(restoreAssetId(["asset-new"], "missing"), "asset-new");
});

test("restores only the workflow that belongs to the selected review", () => {
  const workflows = [
    { id: "newest-other-asset", review_session_id: "review-2" },
    { id: "selected-asset", review_session_id: "review-1" },
  ];
  assert.equal(restoreWorkflow(workflows, "review-1")?.id, "selected-asset");
  assert.equal(restoreWorkflow(workflows, undefined), null);
});

test("explains proxy preparation and recoverable failure without format jargon", () => {
  assert.equal(
    playbackStatusCopy({ mode: "proxy", status: "running" }),
    "Preparing browser preview",
  );
  assert.equal(
    playbackStatusCopy({
      mode: "proxy",
      status: "failed",
      error: "Preview was interrupted. Retry preparation to continue.",
    }),
    "Preview was interrupted. Retry preparation to continue.",
  );
});
