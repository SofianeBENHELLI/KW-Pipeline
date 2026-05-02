# 3DX KnowledgeForge — KW-Pipeline 3DEXPERIENCE widget

A 3DEXPERIENCE 3DDashboard custom widget that surfaces the KW-Pipeline
backend (`apps/api`) inside a dashboard tile:

- Backend health + base URL.
- Recent ingested documents with status badges.
- Knowledge-layer state summary (node/edge counts by kind).
- Upload queue: single file, multi-select, or whole folder
  (`webkitdirectory`) → `POST /documents/upload` with concurrency 2.
- Settings panel to set the API base URL (persisted per tile via
  `widget.setValue`).

The widget follows the dashboard's runtime contract: an XHTML entry, a
bootstrap script that derives `main.js` from `widget.uwaUrl`, and React
rendering inside `widget.addEvent("onLoad")`. See
`~/.claude/skills/3dx-widget/references/react-template-anatomy.md` for
the canonical walkthrough.

## Toolchain note (deviation from the skill template)

The official Widget Lab template depends on six `@widget-lab/*` packages
hosted on the private 3DS GitLab npm registry (`itgit.dsone.3ds.com`).
Reaching that registry requires Reporter access on the `widget-lab`
group, which not every contributor has. To unblock builds, this project:

- Sources `@widget-lab/3ddashboard-utils` via a `file:` dependency
  pointing at a local clone of
  <https://itgit.dsone.3ds.com/widget-lab/libraries/3ddashboard-utils.git>
  at `<worktree>/.kw-pipeline/3ddashboard-utils/` (git-ignored).
- Replaces `@widget-lab/widget-templates-webpack-configs` with a small
  self-contained [`webpack.config.js`](webpack.config.js) that bakes in
  the same essentials (XHTML entry copy, babel-loader for TS/JSX, HTTPS
  dev server on 8081 with `/widget` path).
- Drops the lint/prettier/browserslist `@widget-lab/*` presets in favour
  of inline browser targets in `package.json` and standard tooling.

If a contributor gains group access, they can swap the `file:` dep in
`package.json` for `"@widget-lab/3ddashboard-utils": "2.x"` and
uncomment the registry line in [`.npmrc`](.npmrc); everything else
keeps working.

## Local setup

One-time per worktree — clone `3ddashboard-utils` so the widget's
`file:` dep can resolve:

```bash
mkdir -p .kw-pipeline
cd .kw-pipeline
git clone https://itgit.dsone.3ds.com/widget-lab/libraries/3ddashboard-utils.git
cd 3ddashboard-utils
npx --yes -p typescript@5 tsc --project tsconfig.json   # produces out/
```

The clone uses your itgit credentials. If `git clone` over HTTPS
prompts for a username, configure a Personal Access Token first via
`git credential-osxkeychain` or use SSH (`git@itgit.dsone.3ds.com:…`).

## Local development

```bash
# Terminal 1 — backend (kw-demo defaults already include
# https://localhost:8081 in the CORS allowlist).
make demo-api

# Terminal 2 — widget dev server.
make demo-widget
# or, equivalently:
cd apps/widget && npm install && npm start
```

Open <https://localhost:8081/widget>. Accept the self-signed cert from
`webpack-dev-server`. The widget should:

1. Show a green health dot (or red + error if `make demo-api` is not
   running).
2. List any documents already in the catalog.
3. Show knowledge-layer counts (zero on a fresh demo backend).
4. Accept files via the three Add buttons.

## Production build

```bash
cd apps/widget
npm run build
```

`dist/` will contain `index.html` + `main.js` + the auto-generated
`main.js.LICENSE.txt`. Open `dist/index.html` directly in a browser to
sanity-check that the bundle renders without throwing — the bootstrap
falls back to `uwaPath = "./"` when there is no dashboard host.

## S3 deploy + dashboard registration

Target bucket configured in
`/Users/sxz/Documents/Widget Studio/Widget Bucket/3DXKWFORGEWidgetS3.md`:
`s3://3dx-kwforge-widgets/` in `eu-north-1`. ACLs are disabled on the
bucket — **do NOT pass `--acl public-read`**, the bucket policy already
grants public reads.

```bash
# 1. Upload bundle
aws s3 sync apps/widget/dist/ \
  s3://3dx-kwforge-widgets/3dx-knowledgeforge/v0.1.0/ \
  --region eu-north-1 \
  --cache-control "no-cache" \
  --content-type-by-extension

# 2. Force a friendlier content-type for the XHTML entry
aws s3 cp apps/widget/dist/index.html \
  s3://3dx-kwforge-widgets/3dx-knowledgeforge/v0.1.0/index.html \
  --region eu-north-1 \
  --content-type "text/html" \
  --cache-control "no-cache"

# 3. Verify
curl -I https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledgeforge/v0.1.0/index.html
```

Bucket CORS (S3 console → Permissions → CORS) — needed for the
dashboard host to load `main.js`:

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": ["https://*.3dexperience.3ds.com"],
    "ExposeHeaders": []
  }
]
```

KW-Pipeline backend CORS — append the bucket origin to
`KW_CORS_ALLOWED_ORIGINS` so the widget can call the API:

```
KW_CORS_ALLOWED_ORIGINS=https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com,https://*.3dexperience.3ds.com
```

3DDashboard registration — "Create new widget from URL":

```
https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledgeforge/v0.1.0/index.html
```

(Register `index.html`, **not** `main.js` — the XHTML entry contains
the bootstrap that loads the bundle.)

First-run config: open the widget's settings (⚙) and set the API base
URL to your deployed KW-Pipeline host. Persisted per-tile via
`widget.setValue`.

## Hard rules — do not drift

- XHTML doctype + `xmlns:widget="http://www.netvibes.com/ns/"` in
  `src/index.html`. A plain HTML5 doctype silently breaks the dashboard.
- React `createRoot` runs **only inside** `widget.addEvent("onLoad", …)`
  in `src/index.tsx`. Rendering at module top level produces a blank
  widget.
- No `manifest.json`. The descriptor is `index.html` plus the
  `widget.setTitle("3DX KnowledgeForge")` call in `src/index.tsx`.
- Keep `disableDefaultCSS(true)` — the dashboard's baseline UWA styles
  conflict with the React layout.

## Files

```
apps/widget/
  package.json        webpack.config.js
  babel.config.js     tsconfig.json
  .npmrc              .gitignore          README.md
  src/
    index.html        index.tsx           App.tsx
    styles.css        widget.d.ts
    api/
      client.ts       types.ts
    sections/
      HealthCard.tsx        DocumentsList.tsx
      KnowledgeSummary.tsx  UploadQueue.tsx
    settings/
      SettingsPanel.tsx
```

## API contract

Calls four KW-Pipeline endpoints. Response shapes are mirrored by hand
in `src/api/types.ts` to avoid coupling this package to `apps/web`'s
1000+ line generated OpenAPI schema. If a backend response shape
changes in a way that affects the fields rendered here, update
`types.ts` to match. Endpoints:

| Endpoint | Used by |
| --- | --- |
| `GET /health` | `HealthCard` |
| `GET /documents` (cursor-paginated, `limit=25`) | `DocumentsList` |
| `GET /knowledge/graph` (cursor-paginated) | `KnowledgeSummary` |
| `POST /documents/upload` | `UploadQueue` |
