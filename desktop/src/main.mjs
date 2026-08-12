import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  BrowserWindow,
  app,
  dialog,
  ipcMain,
  shell,
} from "electron";

import {
  assertLoopbackUrl,
  backendLaunch,
  defaultMediaRoots,
  findAvailablePort,
  frontendLaunch,
  isPathInside,
  waitForHttp,
} from "./runtime.mjs";
import { startPackagedFrontend } from "./frontend-server.mjs";

const desktopDirectory = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = path.dirname(desktopDirectory);
const children = new Set();
const localServers = new Set();
let backendUrl = "";
let mainWindow = null;

function startChild(launch) {
  const child = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: launch.env,
    shell: false,
    stdio: "inherit",
  });
  children.add(child);
  child.once("exit", () => children.delete(child));
  return child;
}

function stopChildren() {
  for (const child of children) {
    if (!child.killed) child.kill("SIGTERM");
  }
  children.clear();
  for (const server of localServers) server.close();
  localServers.clear();
}

async function realContainedPath(root, candidate) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) return null;
  try {
    const [realRoot, realCandidate] = await Promise.all([
      fs.realpath(root),
      fs.realpath(candidate),
    ]);
    return isPathInside(realRoot, realCandidate) ? realCandidate : null;
  } catch {
    return null;
  }
}

function registerNativeBridge({ appDataDirectory, projectsDirectory, mediaRoots }) {
  ipcMain.handle("desktop:bootstrap", () => ({ apiUrl: backendUrl }));
  ipcMain.handle("desktop:select-media", async (_event, requestedDefaultPath) => {
    const defaultPath = (
      await Promise.all(
        mediaRoots.map((root) => realContainedPath(root, requestedDefaultPath)),
      )
    ).find(Boolean);
    const result = await dialog.showOpenDialog({
      title: "Choose a recording",
      ...(defaultPath ? { defaultPath } : {}),
      properties: ["openFile"],
      filters: [
        {
          name: "Video recordings",
          extensions: ["mkv", "mp4", "mov", "m4v", "avi", "webm"],
        },
      ],
    });
    if (result.canceled || result.filePaths.length !== 1) return null;
    const selected = result.filePaths[0];
    const permitted = (
      await Promise.all(mediaRoots.map((root) => realContainedPath(root, selected)))
    ).find(Boolean);
    if (!permitted) {
      await dialog.showMessageBox({
        type: "warning",
        title: "Recording is outside an allowed folder",
        message:
          "Choose a recording from Movies, Desktop, or Downloads. Cutroom keeps local-file access intentionally limited.",
      });
      return null;
    }
    return { path: permitted };
  });
  ipcMain.handle("desktop:select-folder", async () => {
    const result = await dialog.showOpenDialog({
      title: "Choose an existing video folder",
      properties: ["openDirectory"],
    });
    if (result.canceled || result.filePaths.length !== 1) return null;
    const selected = result.filePaths[0];
    const permitted = (
      await Promise.all(mediaRoots.map((root) => realContainedPath(root, selected)))
    ).find(Boolean);
    if (!permitted) {
      await dialog.showMessageBox({
        type: "warning",
        title: "Folder is outside an allowed location",
        message:
          "Choose a folder inside Movies, Desktop, or Downloads. Cutroom keeps local-file access intentionally limited.",
      });
      return null;
    }
    return { path: permitted, name: path.basename(permitted) };
  });
  ipcMain.handle("desktop:reveal-output", async (_event, outputPath) => {
    const permitted = (
      await Promise.all([
        realContainedPath(projectsDirectory, outputPath),
        realContainedPath(path.join(appDataDirectory, "projects"), outputPath),
      ])
    ).find(Boolean);
    if (!permitted || path.extname(permitted).toLowerCase() !== ".mp4") {
      throw new Error("The requested output is outside a Cutroom project folder");
    }
    shell.showItemInFolder(permitted);
    return true;
  });
}

async function startDesktop() {
  const backendPort = await findAvailablePort();
  const appDataDirectory = app.getPath("userData");
  const projectsDirectory = path.join(app.getPath("desktop"), "Cutroom Projects");
  const mediaRoots = defaultMediaRoots(os.homedir());
  backendUrl = `http://127.0.0.1:${backendPort}`;
  let uiUrl = process.env.VEA_DESKTOP_UI_URL;
  let frontendPort = null;
  if (!uiUrl) {
    frontendPort = await findAvailablePort();
    uiUrl = `http://127.0.0.1:${frontendPort}`;
  }
  const validatedUiUrl = assertLoopbackUrl(uiUrl);
  const backend = backendLaunch({
    appDataDirectory,
    projectsDirectory,
    backendPort,
    dashboardOrigin: validatedUiUrl.origin,
    mediaRoots,
    repositoryRoot,
    resourcesPath: process.resourcesPath,
    packaged: app.isPackaged,
  });
  startChild(backend);
  await waitForHttp(`${backendUrl}/healthz`, { timeoutMs: 45_000 });

  if (frontendPort !== null) {
    if (app.isPackaged) {
      const frontendRoot = path.join(process.resourcesPath, "frontend");
      const server = await startPackagedFrontend({
        assetRoot: path.join(frontendRoot, "client"),
        host: "127.0.0.1",
        port: frontendPort,
        workerPath: path.join(frontendRoot, "server", "index.js"),
      });
      localServers.add(server);
    } else {
      startChild(frontendLaunch({ frontendPort, repositoryRoot }));
    }
    await waitForHttp(uiUrl, { timeoutMs: 60_000 });
  }

  const allowedUiOrigin = validatedUiUrl.origin;
  registerNativeBridge({ appDataDirectory, projectsDirectory, mediaRoots });
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 1080,
    minHeight: 720,
    title: "Cutroom",
    backgroundColor: "#101316",
    webPreferences: {
      preload: path.join(desktopDirectory, "src", "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, navigationUrl) => {
    if (new URL(navigationUrl).origin !== allowedUiOrigin) event.preventDefault();
  });
  mainWindow.once("closed", () => {
    mainWindow = null;
  });
  await mainWindow.loadURL(uiUrl);
}

app.on("before-quit", stopChildren);
app.on("window-all-closed", () => app.quit());
app
  .whenReady()
  .then(startDesktop)
  .catch(async (error) => {
    stopChildren();
    await dialog.showErrorBox(
      "Cutroom could not start",
      error instanceof Error ? error.message : String(error),
    );
    app.quit();
  });
