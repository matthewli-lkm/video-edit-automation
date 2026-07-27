# Cutroom desktop shell

This package is the completed Batch 3 / G2g desktop-integration slice. One Electron process:

1. reserves private localhost ports;
2. starts the FastAPI backend with desktop-owned data and narrow media roots;
3. waits for the backend health check;
4. starts and waits for the existing dashboard;
5. opens a sandboxed desktop window with a minimal native bridge; and
6. terminates both child processes when the application exits.

The bridge exposes only three operations: read the generated backend address, select a recording
from Movies/Desktop/Downloads, and reveal a completed MP4 inside Cutroom's managed project
workspace. The renderer has no Node.js access and cannot execute commands or reveal arbitrary
paths.

Development launches the repository's Python and dashboard processes. Packaged builds instead use a
single-file Python sidecar and a production dashboard runtime stored inside the application.

## Development

Install the repository prerequisites and frontend dependencies first, then:

```bash
npm --prefix desktop ci
npm --prefix desktop start
```

The desktop shell automatically connects the dashboard to its own backend. Demo mode and manual
backend-address controls remain available only in the browser build.

## Build the macOS application

```bash
uv sync --extra dev --extra packaging
npm --prefix desktop ci
npm --prefix desktop run pack
npm --prefix desktop run dist
```

`pack` creates an unpacked `Cutroom.app`; `dist` creates an ad-hoc-signed, non-notarized DMG under
`desktop/release/`. The package includes the dashboard and compiled API, so end users do not need
Python, Node.js, or the repository.

The Batch 3 build deliberately uses system FFmpeg/ffprobe and optionally Tesseract. The in-app
system check makes missing dependencies visible. Bundled media tools and licence attribution,
application branding, signing, notarization, updates, and clean-Mac release qualification remain
Batch 4 release work.
