#!/usr/bin/env bash
# Update one deployment URL in every repo-root HTML dashboard that
# carries the ``<script id="kw-deployments" type="application/json">…</script>``
# block.
#
# Today that's two files:
#
#   - ``demo.html``                 — local-dev + prod status dashboard
#   - ``kw-pipeline-landing.html``  — French-language landing page,
#                                     deploy-aware cards
#
# Both files use the same JSON shape for their URL block so the same
# sed substitution works on either. To add another file, append it to
# the ``DASHBOARDS`` list below.
#
# Called from each ``scripts/deploy-{widget,explorer,orbital}.sh`` after
# a successful S3 upload, so the dashboards stay in sync with the live
# bundles without anyone hand-editing them. The widget+explorer dev
# loop also passes ``backend`` here when the operator first wires the
# production API hostname.
#
# Usage:
#   scripts/_update-demo-deployment.sh KIND URL
#
# Where KIND is one of: backend | widget | explorer | orbital
#
# The script is idempotent — re-running with the same args is a no-op.
# It exits 0 cleanly when none of the dashboard files are present
# (so a checkout without them doesn't break the deploy).

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

# Files to update. Each file must carry an inline
# ``<script id="kw-deployments" type="application/json">…</script>``
# block with the canonical 2-space-indent JSON shape; see ``demo.html``
# for the reference.
DASHBOARDS=(
  "$REPO_ROOT/demo.html"
  "$REPO_ROOT/kw-pipeline-landing.html"
)

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

# Pre-escape the URL for the post-update grep verification. Same
# escape set the Python re.escape uses for shell-safe regex literals.
URL_RE_ESCAPED="$(printf '%s' "$URL" | sed 's/[.[\*^$/]/\\&/g')"

UPDATED_ANY=false

for DASHBOARD in "${DASHBOARDS[@]}"; do
  if [ ! -f "$DASHBOARD" ]; then
    # File not present in this checkout — skip silently. (The
    # dashboards are opt-in: a contributor cloning the repo only to
    # ship a backend PR doesn't need either of them, and a missing
    # file shouldn't fail the deploy.)
    continue
  fi

  # Skip files that don't actually have the marker block — defensive
  # guard against accidental edits to DASHBOARDS that point at the
  # wrong file.
  if ! grep -q '<script id="kw-deployments"' "$DASHBOARD"; then
    continue
  fi

  # BSD ``sed -i`` requires an extension argument; GNU ``sed -i`` does
  # not. ``sed -i.bak ... && rm bak`` works on both.
  if ! sed -i.bak -E "s|$PATTERN|$REPLACEMENT|" "$DASHBOARD"; then
    echo "✗ sed failed to update $KIND in $DASHBOARD" >&2
    rm -f "$DASHBOARD.bak"
    exit 1
  fi
  rm -f "$DASHBOARD.bak"

  # Sanity-check the edit landed: grep the substitution back out. If
  # it's missing, the regex didn't match (someone reformatted the
  # JSON block) and we should fail loudly rather than ship a stale
  # dashboard.
  if ! grep -qE "^[[:space:]]*\"$KIND\":[[:space:]]+\"${URL_RE_ESCAPED}\"" "$DASHBOARD"; then
    echo "✗ post-update verification failed — $KIND URL not found in $DASHBOARD" >&2
    echo "  (the inline JSON block in $(basename "$DASHBOARD") may have been reformatted;" >&2
    echo "   restore the canonical 2-space indent)" >&2
    exit 1
  fi

  echo "✓ updated $KIND → $URL in $(basename "$DASHBOARD")"
  UPDATED_ANY=true
done

if [ "$UPDATED_ANY" = "false" ]; then
  # All dashboard files are absent — fine, the deploy succeeds anyway.
  # No echo so the deploy log doesn't gain noise on backend-only
  # contributor checkouts.
  exit 0
fi
