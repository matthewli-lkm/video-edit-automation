# Gaming Workflow Console

The frontend is the local review surface for the video-edit automation backend. It is deliberately
a review console, not a general timeline editor.

## Local development

From the repository root:

```bash
uv sync --extra dev
npm --prefix frontend ci
uv run python scripts/export_openapi.py
npm --prefix frontend run api:generate
./scripts/dev-dashboard.sh
```

Open `http://127.0.0.1:4173`. The API remains bound to `127.0.0.1:8765`.

The dashboard supports a realistic demo mode, but real media import, analysis, approval, and
rendering use the local FastAPI service. Agent 2 is optional. Manual review and exact-version human
approval remain available when no local reviewer model is configured.

MKV and browser-incompatible recordings are reviewed through a managed H.264/AAC proxy. The
dashboard displays preparation/retry state, restores preview and final render jobs after refresh,
restores the selected recording, and uses the original only through project-and-asset IDs. Final
downloads come from a backend-owned attachment route.

## Checks

```bash
npm run lint
npm test
```

`openapi.json` and `app/generated/api-schema.d.ts` are generated from the FastAPI application.
Regenerate both after changing an API request or response schema.

## Safety boundary

- The browser sends asset IDs and typed plan data, never FFmpeg commands or output paths.
- Local imports are still validated by the backend media-root policy.
- Source footage cannot be uploaded, deleted, or overwritten from this interface.
- Final rendering remains blocked until the current plan version has explicit human approval.
