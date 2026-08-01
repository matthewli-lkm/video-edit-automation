import net from "node:net";
import path from "node:path";

export function isPathInside(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function defaultMediaRoots(homeDirectory) {
  return ["Movies", "Desktop", "Downloads"].map((name) =>
    path.join(homeDirectory, name),
  );
}

export function desktopExecutablePath({
  resourcesPath,
  packaged,
  platform = process.platform,
  environment = process.env,
}) {
  const entries = [];
  if (packaged) {
    entries.push(path.join(resourcesPath, "media-tools"));
    if (platform === "darwin") {
      entries.push("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin");
    }
  }
  entries.push(...(environment.PATH ?? "").split(path.delimiter));
  return [...new Set(entries.filter(Boolean))].join(path.delimiter);
}

export function assertLoopbackUrl(value) {
  const url = new URL(value);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "::1"].includes(url.hostname)
  ) {
    throw new Error("The desktop dashboard must use a private localhost HTTP address");
  }
  return url;
}

export async function findAvailablePort(host = "127.0.0.1") {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close((error) => {
        if (error) reject(error);
        else if (port === null) reject(new Error("Could not reserve a local port"));
        else resolve(port);
      });
    });
  });
}

export async function waitForHttp(
  url,
  {
    fetchImpl = globalThis.fetch,
    timeoutMs = 30_000,
    intervalMs = 100,
    now = Date.now,
    delay = (milliseconds) =>
      new Promise((resolve) => setTimeout(resolve, milliseconds)),
  } = {},
) {
  const deadline = now() + timeoutMs;
  let lastError = null;
  while (now() < deadline) {
    try {
      const response = await fetchImpl(url);
      if (response.ok) return response;
      lastError = new Error(`Local service returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await delay(intervalMs);
  }
  throw new Error(
    `Local service did not become ready: ${
      lastError instanceof Error ? lastError.message : "unknown error"
    }`,
  );
}

export function backendLaunch({
  appDataDirectory,
  projectsDirectory,
  backendPort,
  dashboardOrigin,
  mediaRoots,
  repositoryRoot,
  resourcesPath,
  packaged,
  platform = process.platform,
  environment = process.env,
}) {
  const executableName =
    platform === "win32" ? "video-edit-automation.exe" : "video-edit-automation";
  const command = packaged
    ? path.join(resourcesPath, "backend", executableName)
    : (environment.VEA_DESKTOP_BACKEND_COMMAND ?? "uv");
  const args = packaged
    ? []
    : [
        "run",
        "uvicorn",
        "video_edit_automation.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        String(backendPort),
      ];
  return {
    command,
    args,
    cwd: packaged ? resourcesPath : repositoryRoot,
    env: {
      ...environment,
      PATH: desktopExecutablePath({
        resourcesPath,
        packaged,
        platform,
        environment,
      }),
      VEA_HOST: "127.0.0.1",
      VEA_PORT: String(backendPort),
      VEA_DATA_DIR: appDataDirectory,
      VEA_PROJECTS_DIR: projectsDirectory,
      VEA_ALLOWED_MEDIA_ROOTS: JSON.stringify(mediaRoots),
      VEA_DASHBOARD_ALLOWED_ORIGINS: JSON.stringify([dashboardOrigin]),
    },
  };
}

export function frontendLaunch({
  frontendPort,
  repositoryRoot,
  environment = process.env,
}) {
  return {
    command: environment.VEA_DESKTOP_FRONTEND_COMMAND ?? "npm",
    args: [
      "--prefix",
      "frontend",
      "run",
      "dev",
      "--",
      "--host",
      "127.0.0.1",
      "--port",
      String(frontendPort),
      "--strictPort",
    ],
    cwd: repositoryRoot,
    env: { ...environment },
  };
}
