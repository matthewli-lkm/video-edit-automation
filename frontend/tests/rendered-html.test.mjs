import assert from "node:assert/strict";
import test from "node:test";

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;

test("renders development preview metadata", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );

  assert.equal(response.status, 200);
  assert.match(
    response.headers.get("content-type") ?? "",
    /^text\/html\b/i,
  );
  const html = await response.text();
  assert.match(html, developmentPreviewMeta);
  assert.match(html, /Cutroom/);
  assert.match(html, /Gaming workflow/);
  assert.match(html, /Choose how the first cut is made/);
  assert.match(html, />Manual</);
  assert.match(html, /Kill &amp; teamfight detection/);
  assert.match(html, /AI assistance/);
  assert.match(html, /No model will be called/);
  assert.match(html, /Kill &amp; teamfight detection/);
  assert.match(html, /Champion kills/);
  assert.match(html, /Multi-kills/);
  assert.match(html, /Nearby kills are combined/);
  assert.doesNotMatch(html, /Tell the editor what matters/);
  assert.match(html, /Publishing and source deletion are disabled/);
  assert.match(html, /Max duration/);
  assert.match(html, /shorter valid reels still continue/);
  assert.match(html, /MM:SS\.s/);
  assert.match(html, /Source overview/);
  assert.match(html, /Current cut/);
  assert.match(html, /Generated reel/);
  assert.match(html, /Whole source/);
  assert.match(html, /Drag to inspect any moment in the recording/);
  assert.match(html, /Highlighted area will be generated/);
  assert.match(html, /Check In/);
  assert.match(html, /Check Out/);
  assert.match(html, /Undo/);
  assert.match(html, /Redo/);
  assert.match(html, /Reset trim/);
  assert.match(html, /Optional Agent 2/);
  assert.match(html, /Independent review · optional/);
  assert.match(html, /Add a missed highlight/);
  assert.match(html, /Use playhead/);
  assert.match(html, /Add to review/);
  assert.match(html, /Drag clip start/);
  assert.match(html, /Drag clip end/);
  assert.match(html, /Approve current plan/);
});
