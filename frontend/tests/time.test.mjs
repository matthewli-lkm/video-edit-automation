import assert from "node:assert/strict";
import test from "node:test";

import {
  clampBoundary,
  createBoundaryWindow,
  formatTimestamp,
  parseTimestamp,
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
