#!/usr/bin/env bash
# Deploy the 3DX Knowledge Explorer widget to S3.
#
# Builds the production bundle in apps/explorer/dist/ and syncs it to
# s3://3dx-kwforge-widgets/3dx-knowledge-explorer/<version>/. The XHTML
# entry gets a forced "text/html" Content-Type so older browsers
# don't choke on "application/xhtml+xml".
#
# Pre-requisites:
#   - `aws` CLI on PATH, configured for the 3DX-KWFORGE AWS account
#     (467685081786) — or any role with s3:PutObject on the bucket.
#   - Node 20+ and npm available so we can run `npm install` + build.
#
# Usage:
#   scripts/deploy-explorer.sh                # uses package.json version
#   scripts/deploy-explorer.sh v0.2.0         # override
#
# The script is idempotent — re-running it overwrites the same prefix.
# To publish a new version without dropping the previous one, bump the
# version arg and the tile lives at a new URL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPLORER_DIR="$REPO_ROOT/apps/explorer"
BUCKET="3dx-kwforge-widgets"
REGION="eu-north-1"
PREFIX="3dx-knowledge-explorer"

# Resolve the version: argv[1] wins; otherwise read package.json.
if [ "${1-}" != "" ]; then
  VERSION="$1"
else
  VERSION="v$(node -p "require('$EXPLORER_DIR/package.json').version")"
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
echo

# 1. Build the production bundle.
cd "$EXPLORER_DIR"
if [ ! -x "$EXPLORER_DIR/node_modules/.bin/webpack" ]; then
  echo "→ installing apps/explorer deps (one-time, ~30s)…"
  npm install --silent --no-fund --no-audit
fi
echo "→ building production bundle…"
npm run --silent build

if [ ! -f "$EXPLORER_DIR/dist/index.html" ] || [ ! -f "$EXPLORER_DIR/dist/main.js" ]; then
  echo "✗ build did not produce dist/index.html and dist/main.js." >&2
  exit 1
fi

# 2. Sync everything except index.html with content-type-by-extension.
#
# AWS CLI v2 defaults `s3 sync` to ``binary/octet-stream`` and only
# guesses MIME from the extension when ``--content-type-by-extension``
# is passed. AWS CLI v1 always guesses by extension and rejects the
# flag as unknown. Detect the major version and only pass the flag on
# v2, so the script works on both.
SYNC_EXTRA=()
if aws --version 2>&1 | grep -qE '^aws-cli/2'; then
  SYNC_EXTRA+=(--content-type-by-extension)
fi

echo "→ syncing dist/ to s3://$BUCKET/$PREFIX/$VERSION/"
aws s3 sync "$EXPLORER_DIR/dist/" \
  "s3://$BUCKET/$PREFIX/$VERSION/" \
  --region "$REGION" \
  --cache-control "no-cache" \
  "${SYNC_EXTRA[@]}" \
  --exclude "index.html"

# 3. Force text/html on the XHTML entry.
echo "→ uploading index.html with Content-Type: text/html"
aws s3 cp "$EXPLORER_DIR/dist/index.html" \
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
  echo
  echo "Register this URL in 3DEXPERIENCE → Run Your App:"
  echo
  echo "  $URL"
  echo
else
  echo "✗ HEAD $URL did not return 200. Check bucket policy + propagation." >&2
  exit 1
fi
