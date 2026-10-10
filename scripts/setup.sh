#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.cache/uv}"
uv sync --locked
mkdir -p .runtime/katago
chmod 700 .runtime
python3 scripts/install_katago.py
if [ ! -f .runtime/analysis.cfg ]; then
  cp config/analysis-cpu.cfg .runtime/analysis.cfg
fi
.runtime/katago/squashfs-root/AppRun version
