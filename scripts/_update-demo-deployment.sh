#!/usr/bin/env bash
# Update one deployment URL in the repo-root ``demo.html``.
#
# Called from each ``scripts/deploy-{widget,explorer,orbital}.sh`` after
# a successful S3 upload, so the dashboard stays in sync with the live
# bundles without anyone hand-editing it. The widget+explorer dev loop
# also passes ``backend`` here when the operator first wires the
# production API hostname.
#
# The ``demo.html`` carries an inline ``<script id="kw-deployments"
# type="application/json">…</script>`` block (verbatim JSON) that
# is the single source of truth for the dashboard's URLs. This
# script edits one key in that block by way of a portable
# ``sed`` invocation that works on both BSD ``sed`` (macOS) and
# GNU ``sed`` (Linux / Windows-via-WSL).
#
# Usage:
#   scripts/_update-demo-deployment.sh KIND URL
#
# Where KIND is one of: backend | widget | explorer | orbital
#
# The script is idempotent — re-running with the same args is a
# no-op. It exits 0 when ``demo.html`` is missing (so a checkout
# without the dashboard doesn't break the deploy).

set -euo pipefail

KIND="${1:-}"
URL="${2:-}"

if [ -z "$KIND" ] || [ -z "$URL" ]; then
  echo "✗ usage: $0 {backend|widget|explorer|orbital} URL" >&2
  exit 2
fi

case "$KIND" in
  backend|widget|explorer|orbital) ;;
  *)
    echo "✗ unknown kind '$KIND' (expected one of: backend widget explorer orbital)" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEMO_HTML="$REPO_ROOT/demo.html"

if [ ! -f "$DEMO_HTML" ]; then
  # No dashboard in this checkout — that's fine, just return cleanly so
  # the deploy script doesn't choke. (The dashboard is opt-in: a
  # contributor cloning the repo only to ship a backend PR doesn't need
  # it.)
  exit 0
fi

# Validate the URL doesn't contain characters that would break the sed
# replacement. We use ``|`` as the sed delimiter so URL slashes are
# fine, but a ``|`` or a literal newline inside the URL would break
# the substitution.
case "$URL" in
  *"|"*|*$'\n'*)
    echo "✗ URL contains characters incompatible with the sed replacement: $URL" >&2
    exit 2
    ;;
esac

# Match the line ``  "<KIND>":  "..."`` inside the JSON block, with
# optional comma trailing. The leading two-space indent is the
# canonical formatting in ``demo.html`` — keep it stable so this
# regex doesn't drift.
PATTERN='^([[:space:]]*"'"$KIND"'":[[:space:]]+")[^"]*(".*)$'
REPLACEMENT='\1'"$URL"'\2'

# BSD ``sed -i`` requires an extension argument; GNU ``sed -i`` does
# not. ``sed -i.bak ... && rm bak`` works on both.
if ! sed -i.bak -E "s|$PATTERN|$REPLACEMENT|" "$DEMO_HTML"; then
  echo "✗ sed failed to update $KIND in $DEMO_HTML" >&2
  rm -f "$DEMO_HTML.bak"
  exit 1
fi
rm -f "$DEMO_HTML.bak"

# Sanity-check the edit landed: grep the substitution back out. If
# it's missing, the regex didn't match (someone reformatted the JSON
# block) and we should fail loudly rather than ship a stale dashboard.
if ! grep -qE "^[[:space:]]*\"$KIND\":[[:space:]]+\"$(printf '%s' "$URL" | sed 's/[.[\*^$/]/\\&/g')\"" "$DEMO_HTML"; then
  echo "✗ post-update verification failed — $KIND URL not found in $DEMO_HTML" >&2
  echo "  (the inline JSON block in demo.html may have been reformatted; restore the canonical 2-space indent)" >&2
  exit 1
fi

echo "✓ updated $KIND → $URL in demo.html"
