import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

test("sandboxed desktop preload uses Electron's supported CommonJS format", async () => {
  const [mainSource, preloadSource] = await Promise.all([
    fs.readFile(path.join(desktopRoot, "src", "main.mjs"), "utf8"),
    fs.readFile(path.join(desktopRoot, "src", "preload.cjs"), "utf8"),
  ]);

  assert.match(mainSource, /preload\.cjs/);
  assert.doesNotMatch(mainSource, /preload\.mjs/);
  assert.match(preloadSource, /require\("electron"\)/);
  assert.match(preloadSource, /exposeInMainWorld\("cutroomDesktop"/);
  assert.match(preloadSource, /desktop:select-folder/);
  assert.match(mainSource, /properties: \["openDirectory"\]/);
  assert.match(mainSource, /Choose an existing video folder/);
});
