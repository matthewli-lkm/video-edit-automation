#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.uv-cache}"
backend_pid=""
frontend_pid=""

cleanup() {
  if [[ -n "$frontend_pid" ]]; then
    kill "$frontend_pid" 2>/dev/null || true
  fi
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

if [[ ! -d "$repo_root/frontend/node_modules" ]]; then
  echo "Frontend dependencies are missing. Run: npm --prefix frontend ci" >&2
  exit 1
fi

(
  cd "$repo_root"
  uv run uvicorn video_edit_automation.main:app \
    --host 127.0.0.1 \
    --port 8765
) &
backend_pid=$!

(
  cd "$repo_root/frontend"
  npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
) &
frontend_pid=$!

echo "Dashboard: http://127.0.0.1:4173"
echo "Backend:   http://127.0.0.1:8765"

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done

echo "A local process stopped unexpectedly." >&2
exit 1
