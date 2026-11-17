#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
exec .venv/bin/python server.py --port "${PORT:-8080}" --inference-url "${YOLO_INFERENCE_URL:-http://100.91.87.119:8090}" "$@"
