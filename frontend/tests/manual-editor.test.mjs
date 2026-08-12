import assert from "node:assert/strict";
import test from "node:test";

import {
  createPlayAroundPlayhead,
  selectedManualClips,
  totalManualDuration,
  validateManualClips,
} from "../app/manual-editor.ts";

test("marks a 30-second play with setup before the playhead", () => {
  assert.deepEqual(createPlayAroundPlayhead(100, 600, 0, "play-1"), {
    id: "play-1",
    title: "Play 1",
    start: 88,
    end: 118,
    selected: true,
  });
});

test("keeps marked plays inside short recordings", () => {
  assert.deepEqual(createPlayAroundPlayhead(4, 20, 1, "play-2"), {
    id: "play-2",
    title: "Play 2",
    start: 0,
    end: 20,
    selected: true,
  });
});

test("sorts only included plays and totals their edited duration", () => {
  const clips = [
    { id: "b", title: "Late", start: 50, end: 65, selected: true },
    { id: "skip", title: "Skip", start: 20, end: 25, selected: false },
    { id: "a", title: "Early", start: 5, end: 15, selected: true },
  ];
  assert.deepEqual(
    selectedManualClips(clips).map((clip) => clip.id),
    ["a", "b"],
  );
  assert.equal(totalManualDuration(clips), 25);
});

test("blocks overlapping selected clips before creating a review plan", () => {
  const clips = [
    { id: "a", title: "First", start: 10, end: 25, selected: true },
    { id: "b", title: "Second", start: 24, end: 40, selected: true },
  ];
  assert.match(validateManualClips(clips, 60) ?? "", /overlaps/);
  assert.equal(
    validateManualClips([{ ...clips[1], start: 25 }], 60),
    null,
  );
});
