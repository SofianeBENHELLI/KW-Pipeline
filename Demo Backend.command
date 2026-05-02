#!/usr/bin/env bash
# Double-click target — opens a Terminal and runs the backend launcher.
#
# macOS treats `.command` files as shell scripts that Finder can launch
# directly. The script just resolves the repo root from its own
# location and execs the underlying launcher in scripts/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/scripts/demo-backend.sh"
