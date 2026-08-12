#!/usr/bin/env bash
set -euo pipefail

desktop_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repository_root="$(cd "${desktop_root}/.." && pwd)"

export UV_CACHE_DIR="${UV_CACHE_DIR:-${repository_root}/.uv-cache}"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-${repository_root}/.pyinstaller-cache}"

mkdir -p "${desktop_root}/build/backend"
mkdir -p "${desktop_root}/build/pyinstaller"

cd "${repository_root}"
uv run --extra packaging pyinstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name video-edit-automation \
  --distpath "${desktop_root}/build/backend" \
  --workpath "${desktop_root}/build/pyinstaller/work" \
  --specpath "${desktop_root}/build/pyinstaller" \
  --collect-all video_edit_automation \
  src/video_edit_automation/desktop_entrypoint.py
