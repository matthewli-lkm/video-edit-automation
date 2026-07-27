import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createAssetFetcher,
  createFrontendFetcher,
} from "../src/frontend-server.mjs";

test("packaged frontend serves immutable assets without a network listener", async (context) => {
  const assetRoot = await fs.mkdtemp(path.join(os.tmpdir(), "cutroom-assets-"));
  context.after(() => fs.rm(assetRoot, { recursive: true, force: true }));
  await fs.mkdir(path.join(assetRoot, "assets"));
  await fs.writeFile(path.join(assetRoot, "assets", "app.js"), "export {};");
  const fetchAsset = createAssetFetcher(assetRoot);

  const asset = await fetchAsset(
    new Request("http://127.0.0.1/assets/app.js"),
  );
  assert.equal(asset.status, 200);
  assert.equal(asset.headers.get("content-type"), "text/javascript; charset=utf-8");
  assert.match(asset.headers.get("cache-control") ?? "", /immutable/);
  assert.equal(await asset.text(), "export {};");

  const missing = await fetchAsset(
    new Request("http://127.0.0.1/assets/missing.js"),
  );
  assert.equal(missing.status, 404);
});

test("packaged frontend serves static files before delegating routes to the worker", async (context) => {
  const assetRoot = await fs.mkdtemp(path.join(os.tmpdir(), "cutroom-assets-"));
  context.after(() => fs.rm(assetRoot, { recursive: true, force: true }));
  await fs.mkdir(path.join(assetRoot, "assets"));
  await fs.writeFile(path.join(assetRoot, "assets", "app.css"), "body { color: white; }");
  const workerRequests = [];
  const fetchFrontend = createFrontendFetcher({
    assetRoot,
    worker: {
      async fetch(request) {
        workerRequests.push(new URL(request.url).pathname);
        return new Response("<main>Cutroom</main>", {
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      },
    },
  });

  const stylesheet = await fetchFrontend(
    new Request("http://127.0.0.1/assets/app.css"),
  );
  assert.equal(stylesheet.status, 200);
  assert.equal(stylesheet.headers.get("content-type"), "text/css; charset=utf-8");
  assert.equal(await stylesheet.text(), "body { color: white; }");
  assert.deepEqual(workerRequests, []);

  const page = await fetchFrontend(new Request("http://127.0.0.1/"));
  assert.equal(page.status, 200);
  assert.equal(await page.text(), "<main>Cutroom</main>");
  assert.deepEqual(workerRequests, ["/"]);
});
