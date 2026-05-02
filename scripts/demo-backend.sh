#!/usr/bin/env bash
# Dummy-proof backend launcher for the KW-Pipeline demo.
#
# Self-contained: bootstraps a Python 3.12 virtualenv, installs the API
# package + test deps if they're missing, then starts uvicorn on
# http://127.0.0.1:8000 with sane CORS for the bundled frontends.
#
# Idempotent — re-running just restarts the server; deps are reused.
#
# Usage:
#   scripts/demo-backend.sh
#   ./Demo Backend.command   (double-click in Finder; calls this script)

set -euo pipefail

# Resolve repo root from this script's location, no matter where it's
# invoked from (Finder double-click runs from $HOME, for example).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv312"
VENV_PY="$VENV_DIR/bin/python"
VENV_KW_DEMO="$VENV_DIR/bin/kw-demo"

# 1. Locate Python 3.12 — preferred, falls back to `python3` if it's 3.12+.
find_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    local ver
    ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [ "$ver" = "3.12" ] || [ "$ver" = "3.13" ] || [ "$ver" = "3.11" ]; then
      command -v python3
      return 0
    fi
  fi
  return 1
}

# 2. Bootstrap the venv if needed.
if [ ! -x "$VENV_PY" ]; then
  echo "→ creating virtualenv at .venv312/"
  PY_BIN="$(find_python)" || {
    echo "✗ Python 3.11 / 3.12 / 3.13 not found on PATH." >&2
    echo "  Install via: brew install python@3.12   (macOS)" >&2
    exit 1
  }
  "$PY_BIN" -m venv "$VENV_DIR"
fi

# 3. Install the API package + test deps if missing.
#    Heuristic: presence of the `kw-demo` console script after install.
if [ ! -x "$VENV_KW_DEMO" ]; then
  echo "→ installing apps/api[test] (one-time, ~30s)…"
  "$VENV_PY" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$VENV_PY" -m pip install --quiet --disable-pip-version-check -e "apps/api[test]"
fi

# 4. CORS allowlist covers every demo frontend we ship plus the
#    standalone widget preview at :5174.
export KW_CORS_ALLOWED_ORIGINS="${KW_CORS_ALLOWED_ORIGINS:-http://localhost:5173,https://localhost:8081,http://127.0.0.1:5174,http://localhost:5174}"

cat <<EOF

╭─ KW-Pipeline backend ────────────────────────────────────────╮
│  API:  http://127.0.0.1:8000
│  Docs: http://127.0.0.1:8000/docs
│  Stop: Ctrl-C in this terminal
╰──────────────────────────────────────────────────────────────╯

EOF

# 5. Hand over to uvicorn via the bundled console script.
exec "$VENV_KW_DEMO"
