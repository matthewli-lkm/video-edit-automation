import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { isPathInside } from "./runtime.mjs";

const CONTENT_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".txt", "text/plain; charset=utf-8"],
  [".woff2", "font/woff2"],
]);

async function assetResponse(assetRoot, request) {
  const requestUrl = new URL(request.url);
  let pathname;
  try {
    pathname = decodeURIComponent(requestUrl.pathname);
  } catch {
    return new Response("Bad Request", { status: 400 });
  }
  const candidate = path.resolve(assetRoot, `.${pathname}`);
  if (!isPathInside(assetRoot, candidate)) {
    return new Response("Not Found", { status: 404 });
  }
  try {
    const data = await fs.readFile(candidate);
    return new Response(request.method === "HEAD" ? null : data, {
      status: 200,
      headers: {
        "cache-control": pathname.includes("/assets/")
          ? "public, max-age=31536000, immutable"
          : "no-cache",
        "content-type":
          CONTENT_TYPES.get(path.extname(candidate).toLowerCase()) ??
          "application/octet-stream",
      },
    });
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "EISDIR") {
      return new Response("Not Found", { status: 404 });
    }
    throw error;
  }
}

export function createAssetFetcher(assetRoot) {
  return (request) => assetResponse(assetRoot, request);
}

export function createFrontendFetcher({ worker, assetRoot }) {
  const fetchAsset = createAssetFetcher(assetRoot);
  const env = {
    ASSETS: {
      fetch: fetchAsset,
    },
    IMAGES: {
      input() {
        throw new Error("Desktop image optimization is unavailable");
      },
    },
  };
  return async (request) => {
    if (request.method === "GET" || request.method === "HEAD") {
      const asset = await fetchAsset(request);
      if (asset.status !== 404) return asset;
    }
    const pending = [];
    const workerResponse = await worker.fetch(request, env, {
      waitUntil(promise) {
        pending.push(promise);
      },
      passThroughOnException() {},
    });
    void Promise.allSettled(pending);
    return workerResponse;
  };
}

async function requestBody(request) {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return chunks.length ? Buffer.concat(chunks) : undefined;
}

async function sendResponse(nodeResponse, response) {
  nodeResponse.statusCode = response.status;
  for (const [name, value] of response.headers) {
    nodeResponse.setHeader(name, value);
  }
  const body = Buffer.from(await response.arrayBuffer());
  nodeResponse.end(body);
}

export function createFrontendHandler({ worker, assetRoot, origin }) {
  const fetchFrontend = createFrontendFetcher({ worker, assetRoot });
  return async (request, response) => {
    try {
      const body = await requestBody(request);
      const fetchRequest = new Request(
        new URL(request.url ?? "/", origin),
        {
          method: request.method,
          headers: request.headers,
          body,
          duplex: body ? "half" : undefined,
        },
      );
      await sendResponse(response, await fetchFrontend(fetchRequest));
    } catch {
      response.statusCode = 500;
      response.setHeader("content-type", "text/plain; charset=utf-8");
      response.end("Cutroom dashboard failed to respond");
    }
  };
}

export async function startPackagedFrontend({
  assetRoot,
  host,
  port,
  workerPath,
}) {
  const { default: worker } = await import(pathToFileURL(workerPath).href);
  if (!worker || typeof worker.fetch !== "function") {
    throw new Error("The packaged dashboard entry point is invalid");
  }
  const origin = `http://${host}:${port}`;
  const server = http.createServer(
    createFrontendHandler({ worker, assetRoot, origin }),
  );
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  return server;
}
