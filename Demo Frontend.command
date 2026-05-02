#!/usr/bin/env bash
# Double-click target — opens a Terminal and runs the frontend launcher.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/scripts/demo-frontend.sh"
