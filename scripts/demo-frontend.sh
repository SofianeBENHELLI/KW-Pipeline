#!/usr/bin/env bash
# Dummy-proof frontend launcher for the KW-Pipeline demo.
#
# Starts the standalone Vite preview that mounts the real widget
# (apps/widget/src/App) in a plain browser tab — no @widget-lab npm
# registry, no 3DEXPERIENCE host required. The browser window IS the
# tile; resize it to see the widget reflow.
#
# Self-contained: installs node_modules under apps/widget-preview/
# on first run, then `vite --port 5174 --host 127.0.0.1` and opens
# the URL in your default browser.
#
# Idempotent — re-running just restarts the server; deps are reused.
#
# Usage:
#   scripts/demo-frontend.sh
#   ./Demo Frontend.command   (double-click in Finder; calls this script)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PREVIEW_DIR="$REPO_ROOT/apps/widget-preview"

cd "$PREVIEW_DIR"

# 1. Verify Node.js is available.
if ! command -v node >/dev/null 2>&1; then
  echo "✗ Node.js not found on PATH." >&2
  echo "  Install via: brew install node    (macOS)" >&2
  exit 1
fi

# 2. Install preview deps if missing.
if [ ! -x "$PREVIEW_DIR/node_modules/.bin/vite" ]; then
  echo "→ installing widget-preview deps (one-time, ~20s)…"
  npm install --silent --no-fund --no-audit
fi

# 2a. Re-run idempotency. If port 5174 is already in use AND the
#     holder is *our own* vite from a previous launch (started off
#     this preview project's node_modules), stop it before starting
#     fresh — otherwise vite reports the port busy and bumps to
#     :5175, which silently breaks demo.html / 3DDashboard registrations
#     pointing at :5174. Foreign processes are left alone.
if command -v lsof >/dev/null 2>&1; then
  HOLDERS="$(lsof -nP -iTCP:5174 -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$HOLDERS" ]; then
    OURS=()
    FOREIGN=()
    for pid in $HOLDERS; do
      cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
      case "$cmd" in
        *"$PREVIEW_DIR"*|*widget-preview*vite*) OURS+=("$pid") ;;
        *) FOREIGN+=("$pid") ;;
      esac
    done
    if [ "${#FOREIGN[@]}" -gt 0 ]; then
      echo "✗ Port 5174 is held by an unrelated process (PIDs: ${FOREIGN[*]})." >&2
      echo "  Kill it manually before re-running this launcher." >&2
      exit 1
    fi
    if [ "${#OURS[@]}" -gt 0 ]; then
      echo "→ stopping previous widget-preview vite on :5174 (PIDs: ${OURS[*]})…"
      kill "${OURS[@]}" 2>/dev/null || true
      for _ in 1 2 3 4 5 6; do
        sleep 0.5
        if [ -z "$(lsof -nP -iTCP:5174 -sTCP:LISTEN -t 2>/dev/null || true)" ]; then
          break
        fi
      done
      STILL="$(lsof -nP -iTCP:5174 -sTCP:LISTEN -t 2>/dev/null || true)"
      if [ -n "$STILL" ]; then
        kill -9 $STILL 2>/dev/null || true
        sleep 0.5
      fi
    fi
  fi
fi

cat <<EOF

╭─ KW-Pipeline widget preview ─────────────────────────────────╮
│  URL:  http://127.0.0.1:5174
│  Reads from: apps/widget/src   (live hot-reload)
│  Stop: Ctrl-C in this terminal
│
│  Tip: run scripts/demo-backend.sh in another terminal so the
│       widget shows real data instead of "Failed to fetch".
╰──────────────────────────────────────────────────────────────╯

EOF

# 3. Open the browser ~1.5s after vite starts (best-effort, macOS).
if command -v open >/dev/null 2>&1; then
  ( sleep 2 && open "http://127.0.0.1:5174" 2>/dev/null || true ) &
fi

# 4. Hand over to vite.
exec "$PREVIEW_DIR/node_modules/.bin/vite" --port 5174 --host 127.0.0.1
