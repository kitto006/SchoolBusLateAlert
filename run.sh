#!/usr/bin/env bash
# Convenience wrapper: runs bus_alert_checker.py using the project's venv,
# regardless of the caller's current directory or activated environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/bus_alert_checker.py" "$@"
