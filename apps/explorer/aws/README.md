# Explorer S3 deployment — bucket configuration

The 3DX Knowledge Explorer widget is hosted at:

> `https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledge-explorer/<version>/index.html`

This is the URL operators paste into 3DEXPERIENCE → **Run Your App** when registering the widget. It shares the bucket with the ingestion widget (`3dx-knowledgeforge`) — both tiles can be installed on the same dashboard.

| Item | Value |
|---|---|
| Bucket | `3dx-kwforge-widgets` |
| Region | `eu-north-1` (Stockholm) |
| AWS account | `467685081786` (3DX-KWFORGE) |
| Public-read | enforced via the bucket policy (ACLs disabled — bucket-owner enforced) |
| Bucket prefix | `3dx-knowledge-explorer/<version>/` |

The bucket already exists and is shared with the ingestion widget — see [`../../widget/aws/README.md`](../../widget/aws/README.md) for the bucket policy and CORS history. **Do NOT recreate the bucket** when deploying the explorer for the first time.

## CORS configuration (already applied)

The same CORS config the ingestion widget uses ([`s3-cors.json`](s3-cors.json), identical contents) is already attached to the bucket. If the bucket is ever recreated, re-apply with:

```bash
aws s3api put-bucket-cors \
  --bucket 3dx-kwforge-widgets \
  --region eu-north-1 \
  --cors-configuration "file://$(git rev-parse --show-toplevel)/apps/explorer/aws/s3-cors.json"
```

### Verify

```bash
curl -s -I -X OPTIONS \
  -H "Origin: https://r1132100968447-eu1-space.3dexperience.3ds.com" \
  -H "Access-Control-Request-Method: GET" \
  https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledge-explorer/v0.1.0/index.html
```

Expect `HTTP/1.1 200 OK` plus an `Access-Control-Allow-Origin: https://r1132100968447-eu1-space.3dexperience.3ds.com` header.

## Deploy

The repo ships a one-shot script — [`scripts/deploy-explorer.sh`](../../../scripts/deploy-explorer.sh) — that builds the production bundle, syncs it to the bucket, and forces the right content type on the XHTML entry. Run from the repo root:

```bash
# Defaults to the version in apps/explorer/package.json (currently 0.1.0).
./scripts/deploy-explorer.sh

# Override version explicitly:
./scripts/deploy-explorer.sh v0.2.0
```

Pre-requisites:

- `aws` CLI on PATH and configured for the `467685081786` account (or any role with `s3:PutObject` on `3dx-kwforge-widgets`).
- Node ≥ 20 + npm available so the script can run `npm install` and `npm run build` inside `apps/explorer/`.

The script is idempotent — re-running it overwrites the same prefix. To publish a new version without dropping the old one, bump the version arg and the new tile lives at a new URL.

## What to register in 3DEXPERIENCE

Register **`index.html`** (the XHTML entry that bootstraps `main.js` via `widget.uwaUrl`):

```
https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledge-explorer/<version>/index.html
```

Do NOT register `main.js` directly — that would skip the Widget-Lab runtime hook the widget needs to call `widget.setTitle()` and `widget.addEvent("onLoad", …)`.

## Upload conventions (recap)

- **Don't** pass `--acl public-read` — ACLs are disabled and the call errors. The bucket policy already grants public `s3:GetObject` to `*`.
- Force `text/html` on the index so older browsers don't choke on `application/xhtml+xml` (the script already does this).
- Layout: `s3://3dx-kwforge-widgets/3dx-knowledge-explorer/<version>/{index.html,main.js,...}`.
