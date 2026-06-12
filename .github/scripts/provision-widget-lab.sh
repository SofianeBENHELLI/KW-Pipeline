#!/usr/bin/env bash
# Provision @widget-lab/3ddashboard-utils for a CI build.
#
# The package is DS-internal (see apps/widget/.npmrc) and the repo's
# package.json files reference it as
# ``file:../../.kw-pipeline/3ddashboard-utils`` — a clone that exists
# on operator workstations but not in this repository. CI builds of
# apps/widget and apps/explorer therefore need one of two sources:
#
#   1. WIDGET_LAB_REPO (+ optional WIDGET_LAB_REPO_TOKEN for private
#      mirrors): "owner/repo" of a GitHub mirror of the package
#      source. Cloned into .kw-pipeline/3ddashboard-utils so the
#      existing file: dependency resolves untouched.
#
#   2. WIDGET_LAB_REGISTRY_TOKEN: auth token for the DS GitLab npm
#      registry. The script rewrites the app's dependency to the
#      registry version (workspace-only mutation — never committed).
#
# Usage: provision-widget-lab.sh <app-dir>   (e.g. apps/widget)

set -euo pipefail

APP_DIR="${1:?usage: provision-widget-lab.sh <app-dir>}"
ROOT_DIR="$(pwd)"
TARGET="$ROOT_DIR/.kw-pipeline/3ddashboard-utils"
DS_REGISTRY="https://itgit.dsone.3ds.com/api/v4/packages/npm/"

if [ -n "${WIDGET_LAB_REPO:-}" ]; then
  echo "→ cloning $WIDGET_LAB_REPO into .kw-pipeline/3ddashboard-utils"
  mkdir -p "$ROOT_DIR/.kw-pipeline"
  if [ -n "${WIDGET_LAB_REPO_TOKEN:-}" ]; then
    git clone --depth 1 \
      "https://x-access-token:${WIDGET_LAB_REPO_TOKEN}@github.com/${WIDGET_LAB_REPO}.git" \
      "$TARGET"
  else
    git clone --depth 1 "https://github.com/${WIDGET_LAB_REPO}.git" "$TARGET"
  fi
  if [ ! -f "$TARGET/package.json" ]; then
    echo "::error::$WIDGET_LAB_REPO does not look like an npm package (no package.json at its root)."
    exit 1
  fi
  echo "✓ file: dependency target provisioned"
  exit 0
fi

if [ -n "${WIDGET_LAB_REGISTRY_TOKEN:-}" ]; then
  echo "→ rewiring @widget-lab/3ddashboard-utils to the DS GitLab registry"
  cd "$APP_DIR"
  {
    echo "@widget-lab:registry=$DS_REGISTRY"
    echo "${DS_REGISTRY#https:}:_authToken=\${WIDGET_LAB_REGISTRY_TOKEN}"
  } >> .npmrc
  npm pkg set 'dependencies.@widget-lab/3ddashboard-utils=*'
  echo "✓ registry configured; npm install will pull the published version"
  exit 0
fi

echo "::error::@widget-lab/3ddashboard-utils is unavailable in CI. Set the WIDGET_LAB_REPO repository variable (GitHub mirror of the package source, plus WIDGET_LAB_REPO_TOKEN secret if private) or the WIDGET_LAB_REGISTRY_TOKEN secret (DS GitLab npm registry). See .github/workflows/deploy-frontends.yml header."
exit 1
