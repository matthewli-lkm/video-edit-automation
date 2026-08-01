import assert from "node:assert/strict";
import test from "node:test";

import {
  runSequentialTasks,
  waitForTerminalJob,
} from "../app/export-workflow.ts";

test("keeps tracking a render beyond the old one-minute polling limit", async () => {
  let polls = 0;
  const updates = [];
  const completed = await waitForTerminalJob(
    async () => ({
      id: "long-render",
      status: ++polls > 100 ? "succeeded" : "running",
    }),
    (job) => updates.push(job.status),
    async () => undefined,
  );

  assert.equal(completed.status, "succeeded");
  assert.equal(polls, 101);
  assert.equal(updates.length, 101);
});

test("runs multiple export jobs one at a time", async () => {
  let active = 0;
  let maximumActive = 0;
  const completionOrder = [];

  const results = await runSequentialTasks(["combined", "clip-1", "clip-2"], async (item) => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await Promise.resolve();
    completionOrder.push(item);
    active -= 1;
    return `${item}-ready`;
  });

  assert.equal(maximumActive, 1);
  assert.deepEqual(completionOrder, ["combined", "clip-1", "clip-2"]);
  assert.deepEqual(results, ["combined-ready", "clip-1-ready", "clip-2-ready"]);
});
