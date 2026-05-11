#!/usr/bin/env bash
# Deploy the Orbital reviewer/admin frontend to S3.
#
# Mirrors scripts/deploy-explorer.sh and scripts/deploy-widget.sh, but
# Orbital is Vite (not webpack) so:
#   - The build outputs ``apps/web/dist/index.html`` plus a multi-file
#     ``apps/web/dist/assets/`` tree of content-hashed JS / CSS chunks.
#   - The build-time backend URL is read from ``VITE_API_BASE_URL``
#     (Vite's standard ``import.meta.env.VITE_*`` injection) instead
#     of ``KW_API_BASE_URL``.
#   - ``apps/web/dist/stats.html`` (rollup-plugin-visualizer treemap) is
#     a developer debug artifact — excluded from the upload so the
#     production prefix stays clean.
#
# The deployed bundle is a regular SPA, not a 3DEXPERIENCE tile — there
# is no ``widget.uwaUrl`` bootstrap in ``index.html`` and the host can
# load it directly. The S3 prefix lives on the same bucket as the two
# tiles so the AWS account / IAM / bucket policy story stays single.
#
# Pre-requisites:
#   - `aws` CLI on PATH, configured for the 3DX-KWFORGE AWS account
#     (467685081786) — or any role with s3:PutObject on the bucket.
#   - Node 22+ and npm available so we can run `npm install` + build.
#
# Required env var:
#   VITE_API_BASE_URL  — backend URL the deployed Orbital calls. Without
#                        this, the build falls back to http://localhost:8000
#                        and the deployed admin surface can never reach a
#                        real backend (it tries localhost from inside the
#                        operator's browser, which obviously fails). The
#                        script refuses to deploy without it set unless
#                        --allow-localhost-fallback is passed for testing.
#
# Usage:
#   VITE_API_BASE_URL=https://kw-api.example.org scripts/deploy-orbital.sh
#   VITE_API_BASE_URL=https://kw-api.example.org scripts/deploy-orbital.sh v0.0.1
#   scripts/deploy-orbital.sh --allow-localhost-fallback   # dev-only
#
# The script is idempotent — re-running it overwrites the same prefix.
# To publish a new version without dropping the previous one, bump the
# version arg and the new build lives at a new URL.

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

# VITE_API_BASE_URL is the load-bearing piece of the deploy. Without it
# the resulting bundle is an unconfigured Orbital that the operator will
# spend hours debugging — fail fast.
if [ -z "${VITE_API_BASE_URL:-}" ]; then
  if [ "$ALLOW_LOCALHOST" = "true" ]; then
    echo "⚠ VITE_API_BASE_URL not set; building with the http://localhost:8000 fallback (--allow-localhost-fallback was passed)." >&2
    echo "  This bundle will only work when the operator's browser can reach the API on localhost." >&2
  else
    echo "✗ VITE_API_BASE_URL must be set so the Orbital bundle knows which backend to call." >&2
    echo "  Example:" >&2
    echo "    VITE_API_BASE_URL=https://kw-api.example.org scripts/deploy-orbital.sh" >&2
    echo "" >&2
    echo "  Pass --allow-localhost-fallback for a dev-only sanity build that hits http://localhost:8000." >&2
    exit 1
  fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ORBITAL_DIR="$REPO_ROOT/apps/web"
BUCKET="3dx-kwforge-widgets"
REGION="eu-north-1"
PREFIX="3dx-knowledge-orbital"

# Resolve the version: argv[1] wins; otherwise read package.json.
# Orbital's package.json starts at 0.0.0 so the first deploy lives at
# v0.0.0 — bump the package.json version to publish to a new URL.
if [ "${1-}" != "" ]; then
  VERSION="$1"
else
  VERSION="v$(node -p "require('$ORBITAL_DIR/package.json').version")"
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
echo "  VITE_API_BASE_URL: ${VITE_API_BASE_URL:-<localhost fallback>}"
echo

# 1. Build the production bundle.
cd "$ORBITAL_DIR"
if [ ! -x "$ORBITAL_DIR/node_modules/.bin/vite" ]; then
  echo "→ installing apps/web deps (one-time, ~30s)…"
  npm install --silent --no-fund --no-audit
fi
echo "→ building production bundle…"
npm run --silent build

if [ ! -f "$ORBITAL_DIR/dist/index.html" ] || [ ! -d "$ORBITAL_DIR/dist/assets" ]; then
  echo "✗ build did not produce dist/index.html and dist/assets/." >&2
  exit 1
fi

# 2. Sync everything except index.html (entry needs a forced
# Content-Type below) and stats.html (rollup-plugin-visualizer
# debug artifact — not for production). AWS CLI's ``s3 sync``
# guesses MIME from the extension via Python's ``mimetypes`` module
# by default for both v1 and v2.
echo "→ syncing dist/ to s3://$BUCKET/$PREFIX/$VERSION/"
aws s3 sync "$ORBITAL_DIR/dist/" \
  "s3://$BUCKET/$PREFIX/$VERSION/" \
  --region "$REGION" \
  --cache-control "no-cache" \
  --exclude "index.html" \
  --exclude "stats.html"

# 3. Force text/html on the entry document.
echo "→ uploading index.html with Content-Type: text/html"
aws s3 cp "$ORBITAL_DIR/dist/index.html" \
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
  # when ``demo.html`` is missing.
  "$SCRIPT_DIR/_update-demo-deployment.sh" orbital "$URL" || true
  echo
  echo "Open Orbital at:"
  echo
  echo "  $URL"
  echo
else
  echo "✗ HEAD $URL did not return 200. Check bucket policy + propagation." >&2
  exit 1
fi
