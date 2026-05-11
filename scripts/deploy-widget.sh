#!/usr/bin/env bash
# Deploy the 3DX KnowledgeForge ingestion widget to S3.
#
# Mirrors scripts/deploy-explorer.sh. Builds the production bundle in
# apps/widget/dist/ and syncs it to
# s3://3dx-kwforge-widgets/3dx-knowledgeforge/<version>/. The XHTML
# entry gets a forced "text/html" Content-Type so older browsers
# don't choke on "application/xhtml+xml".
#
# Pre-requisites:
#   - `aws` CLI on PATH, configured for the 3DX-KWFORGE AWS account
#     (467685081786) — or any role with s3:PutObject on the bucket.
#   - Node 20+ and npm available so we can run `npm install` + build.
#
# Required env var:
#   KW_API_BASE_URL   — backend URL the deployed bundle calls. Without
#                       this, the build falls back to http://localhost:8000
#                       and the deployed widget can never reach a real
#                       backend (it tries localhost from inside
#                       3DDashboard, which obviously fails). The script
#                       refuses to deploy without it set unless
#                       --allow-localhost-fallback is passed for testing.
#
# Usage:
#   KW_API_BASE_URL=https://kw-api.example.org scripts/deploy-widget.sh
#   KW_API_BASE_URL=https://kw-api.example.org scripts/deploy-widget.sh v0.2.0
#   scripts/deploy-widget.sh --allow-localhost-fallback   # dev-only
#
# The script is idempotent — re-running it overwrites the same prefix.
# To publish a new version without dropping the previous one, bump the
# version arg and the tile lives at a new URL.

set -euo pipefail

# Parse --allow-localhost-fallback before positional args so an operator
# can pass it in either order without the version-detection logic
# tripping on it.
ALLOW_LOCALHOST=false
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --allow-localhost-fallback) ALLOW_LOCALHOST=true ;;
    *) ARGS+=("$arg") ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

# KW_API_BASE_URL is the load-bearing piece of the deploy. Without it
# the resulting bundle is an unconfigured tile that the operator will
# spend hours debugging — fail fast.
if [ -z "${KW_API_BASE_URL:-}" ]; then
  if [ "$ALLOW_LOCALHOST" = "true" ]; then
    echo "⚠ KW_API_BASE_URL not set; building with the http://localhost:8000 fallback (--allow-localhost-fallback was passed)." >&2
    echo "  This bundle will only work when 3DDashboard runs on the same host as the API." >&2
  else
    echo "✗ KW_API_BASE_URL must be set so the widget bundle knows which backend to call." >&2
    echo "  Example:" >&2
    echo "    KW_API_BASE_URL=https://kw-api.example.org scripts/deploy-widget.sh" >&2
    echo "" >&2
    echo "  Pass --allow-localhost-fallback for a dev-only sanity build that hits http://localhost:8000." >&2
    exit 1
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WIDGET_DIR="$REPO_ROOT/apps/widget"
BUCKET="3dx-kwforge-widgets"
REGION="eu-north-1"
PREFIX="3dx-knowledgeforge"

# Resolve the version: argv[1] wins; otherwise read package.json.
if [ "${1-}" != "" ]; then
  VERSION="$1"
else
  VERSION="v$(node -p "require('$WIDGET_DIR/package.json').version")"
fi

# Sanity checks.
if ! command -v aws >/dev/null 2>&1; then
  echo "✗ aws CLI not found on PATH." >&2
  echo "  Install via: brew install awscli  (macOS) or pip install awscli  (any)" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "✗ Node.js not found on PATH." >&2
  exit 1
fi
if ! aws sts get-caller-identity --region "$REGION" >/dev/null 2>&1; then
  echo "✗ aws CLI cannot resolve credentials." >&2
  echo "  Run 'aws configure' or export AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY." >&2
  exit 1
fi

echo "→ Deploying $PREFIX@$VERSION to s3://$BUCKET/ in $REGION"
echo "  KW_API_BASE_URL: ${KW_API_BASE_URL:-<localhost fallback>}"
echo

# 1. Build the production bundle.
cd "$WIDGET_DIR"
if [ ! -x "$WIDGET_DIR/node_modules/.bin/webpack" ]; then
  echo "→ installing apps/widget deps (one-time, ~30s)…"
  npm install --silent --no-fund --no-audit
fi
echo "→ building production bundle…"
npm run --silent build

if [ ! -f "$WIDGET_DIR/dist/index.html" ] || [ ! -f "$WIDGET_DIR/dist/main.js" ]; then
  echo "✗ build did not produce dist/index.html and dist/main.js." >&2
  exit 1
fi

# 2. Sync everything except index.html. AWS CLI (both v1 and v2)
# already guesses MIME from the extension via Python's ``mimetypes``
# module by default for ``s3 sync`` — there's no flag to pass.
echo "→ syncing dist/ to s3://$BUCKET/$PREFIX/$VERSION/"
aws s3 sync "$WIDGET_DIR/dist/" \
  "s3://$BUCKET/$PREFIX/$VERSION/" \
  --region "$REGION" \
  --cache-control "no-cache" \
  --exclude "index.html"

# 3. Force text/html on the XHTML entry.
echo "→ uploading index.html with Content-Type: text/html"
aws s3 cp "$WIDGET_DIR/dist/index.html" \
  "s3://$BUCKET/$PREFIX/$VERSION/index.html" \
  --region "$REGION" \
  --content-type "text/html" \
  --cache-control "no-cache"

# 4. Verify the entry is reachable.
URL="https://$BUCKET.s3.$REGION.amazonaws.com/$PREFIX/$VERSION/index.html"
echo
echo "→ verifying $URL"
if curl -fsI "$URL" >/dev/null 2>&1; then
  echo "✓ deploy ok"
  # 5. Update the repo-root demo.html so its "Production deploys"
  # tile points at this version. Best-effort — the helper exits 0
  # when ``demo.html`` is missing (contributors shipping a backend-only
  # PR don't need the dashboard).
  "$SCRIPT_DIR/_update-demo-deployment.sh" widget "$URL" || true
  echo
  echo "Register this URL in 3DEXPERIENCE → Run Your App:"
  echo
  echo "  $URL"
  echo
else
  echo "✗ HEAD $URL did not return 200. Check bucket policy + propagation." >&2
  exit 1
fi
