import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  assertLoopbackUrl,
  backendLaunch,
  defaultMediaRoots,
  frontendLaunch,
  isPathInside,
  waitForHttp,
} from "../src/runtime.mjs";

test("desktop UI addresses are restricted to loopback HTTP", () => {
  assert.equal(
    assertLoopbackUrl("http://127.0.0.1:4173").origin,
    "http://127.0.0.1:4173",
  );
  assert.equal(
    assertLoopbackUrl("http://localhost:4173").origin,
    "http://localhost:4173",
  );
  assert.throws(
    () => assertLoopbackUrl("https://dashboard.example.com"),
    /private localhost/,
  );
  assert.throws(
    () => assertLoopbackUrl("file:///tmp/dashboard.html"),
    /private localhost/,
  );
});

test("path containment rejects siblings and traversal", () => {
  const root = path.resolve("/tmp/cutroom/projects");
  assert.equal(isPathInside(root, path.join(root, "one", "final.mp4")), true);
  assert.equal(isPathInside(root, root), true);
  assert.equal(isPathInside(root, path.resolve("/tmp/cutroom/projects-old/file.mp4")), false);
  assert.equal(isPathInside(root, path.join(root, "..", "secret.mp4")), false);
});

test("default media roots remain intentionally narrow", () => {
  assert.deepEqual(defaultMediaRoots("/Users/editor"), [
    "/Users/editor/Movies",
    "/Users/editor/Desktop",
    "/Users/editor/Downloads",
  ]);
});

test("development backend launch uses argument lists and desktop-owned data", () => {
  const launch = backendLaunch({
    appDataDirectory: "/tmp/cutroom-data",
    backendPort: 43123,
    dashboardOrigin: "http://127.0.0.1:4179",
    mediaRoots: ["/Users/editor/Movies"],
    repositoryRoot: "/repo",
    resourcesPath: "/resources",
    packaged: false,
    environment: {},
  });
  assert.equal(launch.command, "uv");
  assert.deepEqual(launch.args, [
    "run",
    "uvicorn",
    "video_edit_automation.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "43123",
  ]);
  assert.equal(launch.cwd, "/repo");
  assert.equal(launch.env.VEA_DATA_DIR, "/tmp/cutroom-data");
  assert.equal(launch.env.VEA_ALLOWED_MEDIA_ROOTS, '["/Users/editor/Movies"]');
  assert.equal(
    launch.env.VEA_DASHBOARD_ALLOWED_ORIGINS,
    '["http://127.0.0.1:4179"]',
  );
});

test("packaged backend resolves to a bundled executable", () => {
  const launch = backendLaunch({
    appDataDirectory: "/tmp/cutroom-data",
    backendPort: 43123,
    dashboardOrigin: "http://127.0.0.1:4179",
    mediaRoots: [],
    repositoryRoot: "/repo",
    resourcesPath: "/Applications/Cutroom.app/Contents/Resources",
    packaged: true,
    platform: "darwin",
    environment: {},
  });
  assert.equal(
    launch.command,
    "/Applications/Cutroom.app/Contents/Resources/backend/video-edit-automation",
  );
  assert.deepEqual(launch.args, []);
});

test("frontend launch is explicit and does not use a shell command", () => {
  const launch = frontendLaunch({
    frontendPort: 4179,
    repositoryRoot: "/repo",
    environment: {},
  });
  assert.equal(launch.command, "npm");
  assert.deepEqual(launch.args.slice(-5), [
    "--host",
    "127.0.0.1",
    "--port",
    "4179",
    "--strictPort",
  ]);
});

test("health wait retries until the service responds", async () => {
  let attempts = 0;
  let clock = 0;
  const response = await waitForHttp("http://127.0.0.1/healthz", {
    fetchImpl: async () => {
      attempts += 1;
      if (attempts < 3) throw new Error("not ready");
      return new Response("ok", { status: 200 });
    },
    timeoutMs: 1_000,
    intervalMs: 10,
    now: () => clock,
    delay: async (milliseconds) => {
      clock += milliseconds;
    },
  });
  assert.equal(response.status, 200);
  assert.equal(attempts, 3);
});

test("health wait reports the final readiness error", async () => {
  let clock = 0;
  await assert.rejects(
    waitForHttp("http://127.0.0.1/healthz", {
      fetchImpl: async () => new Response("busy", { status: 503 }),
      timeoutMs: 20,
      intervalMs: 10,
      now: () => clock,
      delay: async (milliseconds) => {
        clock += milliseconds;
      },
    }),
    /returned 503/,
  );
});
