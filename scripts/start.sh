#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn server.app:create_event_app --factory --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --workers 1
