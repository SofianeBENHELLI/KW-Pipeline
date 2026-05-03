# Widget S3 deployment — bucket configuration

The 3DX KnowledgeForge widget is hosted at:

> `https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledgeforge/<version>/index.html`

This is the URL operators paste into 3DEXPERIENCE → **Run Your App** when registering the widget. Bucket details:

| Item | Value |
|---|---|
| Bucket | `3dx-kwforge-widgets` |
| Region | `eu-north-1` (Stockholm) |
| AWS account | `467685081786` (3DX-KWFORGE) |
| Public-read | enforced via the bucket policy (ACLs disabled — bucket-owner enforced) |

## CORS configuration (required)

Without this, 3DEXPERIENCE refuses to load `main.js` cross-origin and the widget tile renders empty.

Apply once per bucket (it persists):

```bash
aws s3api put-bucket-cors \
  --bucket 3dx-kwforge-widgets \
  --region eu-north-1 \
  --cors-configuration "file://$(git rev-parse --show-toplevel)/apps/widget/aws/s3-cors.json"
```

Or in the AWS Console: **S3 → 3dx-kwforge-widgets → Permissions → Cross-origin resource sharing (CORS) → Edit** and paste the contents of [`s3-cors.json`](s3-cors.json).

### Verify

```bash
curl -s -I -X OPTIONS \
  -H "Origin: https://r1132100968447-eu1-space.3dexperience.3ds.com" \
  -H "Access-Control-Request-Method: GET" \
  https://3dx-kwforge-widgets.s3.eu-north-1.amazonaws.com/3dx-knowledgeforge/v0.1.0/index.html
```

Expect `HTTP/1.1 200 OK` plus an `Access-Control-Allow-Origin: https://r1132100968447-eu1-space.3dexperience.3ds.com` header. A `403 Forbidden` means CORS is still unset.

## Upload conventions

- **Don't** pass `--acl public-read` to `aws s3 sync` / `aws s3 cp` — ACLs are disabled and the call errors. The bucket policy already grants public `s3:GetObject` to `*`.
- Force the `index.html` content type explicitly so older browsers don't choke on `application/xhtml+xml`:
  ```bash
  aws s3 cp index.html "s3://3dx-kwforge-widgets/3dx-knowledgeforge/<version>/index.html" \
    --content-type "text/html" --region eu-north-1
  ```
- Layout: `s3://3dx-kwforge-widgets/<widget-name>/<version>/{index.html,main.js,...}`.

## What to register in 3DEXPERIENCE

Register **`index.html`** (the XHTML entry that bootstraps `main.js` via `widget.uwaUrl`). Do NOT register `main.js` directly — that would skip the Widget-Lab runtime hook the widget needs.
