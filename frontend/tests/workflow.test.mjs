import assert from "node:assert/strict";
import test from "node:test";

import { deriveWorkflowSteps } from "../app/workflow.ts";

const completeReview = {
  sourceReady: true,
  briefReady: true,
  analysisReady: true,
  decisionsComplete: true,
  hasUnsavedChanges: false,
  planRevisionNeeded: false,
  humanApproved: true,
  finalRenderSucceeded: false,
};

test("never marks export complete before the final render succeeds", () => {
  const steps = deriveWorkflowSteps(completeReview);
  assert.deepEqual(steps.at(-1), {
    label: "Export",
    complete: false,
    current: true,
  });
});

test("unsaved trims return the workflow to review", () => {
  const steps = deriveWorkflowSteps({
    ...completeReview,
    hasUnsavedChanges: true,
  });
  assert.equal(steps[3].current, true);
  assert.equal(steps[3].complete, false);
  assert.equal(steps[4].current, false);
});

test("final export completes only after exact-plan review and rendering", () => {
  const steps = deriveWorkflowSteps({
    ...completeReview,
    finalRenderSucceeded: true,
  });
  assert.equal(steps[3].complete, true);
  assert.equal(steps[4].complete, true);
});
