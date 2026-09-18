#!/usr/bin/env bash
# Runs the tracker from any folder (used by the daily schedule on Mac / Linux).
cd "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi
exec "$PY" reel_stats.py "$@"
