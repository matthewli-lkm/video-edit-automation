#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.uv-cache}"

cd "$repo_root"
uv run ruff check .
uv run pytest
uv run python scripts/export_openapi.py

cd "$repo_root/frontend"
npm run api:generate
npm run lint
npm test

cd "$repo_root"
git diff --exit-code -- \
  frontend/openapi.json \
  frontend/app/generated/api-schema.d.ts
