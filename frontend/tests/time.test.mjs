import assert from "node:assert/strict";
import test from "node:test";

import {
  clampBoundary,
  createBoundaryWindow,
  formatTimestamp,
  moveClipRange,
  parseTimestamp,
  positionRangeInWindow,
} from "../app/time.ts";

test("formats boundary timestamps as MM:SS.s", () => {
  assert.equal(formatTimestamp(1014.8), "16:54.8");
  assert.equal(formatTimestamp(65), "01:05.0");
});

test("parses MM:SS.s and plain seconds", () => {
  assert.equal(parseTimestamp("16:54.8"), 1014.8);
  assert.equal(parseTimestamp("75.2"), 75.2);
  assert.equal(parseTimestamp("1:60"), null);
  assert.equal(parseTimestamp("not a time"), null);
});

test("keeps dragged cut boundaries ordered and inside the source", () => {
  assert.equal(clampBoundary("start", 80, 10, 60, 120), 59.9);
  assert.equal(clampBoundary("start", -5, 10, 60, 120), 0);
  assert.equal(clampBoundary("end", 5, 10, 60, 120), 10.1);
  assert.equal(clampBoundary("end", 150, 10, 60, 120), 120);
});

test("moves a whole clip without changing its duration or leaving the source", () => {
  assert.deepEqual(moveClipRange(10, 20, 5.4, 60), {
    start: 15.4,
    end: 25.4,
  });
  assert.deepEqual(moveClipRange(10, 20, -50, 60), {
    start: 0,
    end: 10,
  });
  assert.deepEqual(moveClipRange(50, 60, 25, 60), {
    start: 50,
    end: 60,
  });
});

test("creates a zoomed boundary window with source-safe context", () => {
  assert.deepEqual(createBoundaryWindow(1014.8, 1056.6, 1938), {
    start: 984.8,
    end: 1086.6,
  });
  assert.deepEqual(createBoundaryWindow(8, 20, 25), {
    start: 0,
    end: 25,
  });
});

test("positions edits inside a stable trim window instead of re-centering them", () => {
  const original = positionRangeInWindow(1000, 1030, 970, 1060);
  const extended = positionRangeInWindow(990, 1030, 970, 1060);
  assert.ok(Math.abs(original.left - 33.333) < 0.001);
  assert.ok(Math.abs(original.width - 33.333) < 0.001);
  assert.ok(Math.abs(extended.left - 22.222) < 0.001);
  assert.ok(Math.abs(extended.width - 44.444) < 0.001);
  assert.ok(extended.left < original.left);
  assert.ok(extended.width > original.width);
});
