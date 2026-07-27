import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("desktop onboarding separates new projects from existing folders", async () => {
  const source = await readFile(
    new URL("../app/page.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /Create new project/);
  assert.match(source, /Open existing video folder/);
  assert.match(source, /Choose folder in Finder/);
  assert.match(source, /Create & enter/);
  assert.match(source, /Upload a recording to begin/);
  assert.match(source, /Add another recording/);
  assert.match(source, /Cutroom lost its local connection/);
  assert.doesNotMatch(source, /Bring one recording\. Leave with clean clips/);
  assert.doesNotMatch(source, /Choose your recording/);
  assert.match(source, /setShowSetup\(false\)/);
});
