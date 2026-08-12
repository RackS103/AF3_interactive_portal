#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 submitter_app/server.py --host "${AF3_SUBMITTER_HOST:-127.0.0.1}" --port "${AF3_SUBMITTER_PORT:-8766}"
