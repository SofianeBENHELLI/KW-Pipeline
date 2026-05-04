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

## Credentials setup

> **Never commit real credentials.** The repo's `.gitignore` blocks
> common credential paths and the pre-commit gitleaks hook scans
> staged files for `AKIA…` patterns; if a real key slips into a
> staged file the commit fails. Treat that as a feature, not a
> nuisance.

### 1. Create a least-privilege IAM identity

Attach the policy in [`iam-policy.json`](iam-policy.json) — and only that
policy — to a dedicated IAM user (e.g. `kw-explorer-deploy`) or a role
your CI assumes. The policy grants:

- `s3:ListBucket` scoped to the `3dx-knowledge-explorer/` prefix
- `s3:PutObject` / `s3:GetObject` / `s3:DeleteObject` /
  `s3:AbortMultipartUpload` on `3dx-knowledge-explorer/*` only
- `sts:GetCallerIdentity` (used by the deploy script's pre-flight)

It cannot touch the ingestion widget's prefix, modify CORS, or
delete the bucket.

CLI shortcut:

```bash
aws iam create-policy \
  --policy-name KWExplorerS3Deploy \
  --policy-document file://apps/explorer/aws/iam-policy.json

aws iam attach-user-policy \
  --user-name kw-explorer-deploy \
  --policy-arn arn:aws:iam::467685081786:policy/KWExplorerS3Deploy
```

### 2. Generate access keys, store them off-repo

After the policy is attached, create access keys for the IAM user
(AWS Console → IAM → Users → *user* → Security credentials → Create
access key → "Command Line Interface (CLI)"). Two equally-good ways
to make them available to the deploy script:

**Option A — `~/.aws/credentials` (preferred, persistent across runs)**

[`credentials.template`](credentials.template) is the exact file
shape. Copy it OUTSIDE the repo and fill in the real values:

```bash
mkdir -p ~/.aws
cp apps/explorer/aws/credentials.template ~/.aws/credentials
chmod 600 ~/.aws/credentials
$EDITOR ~/.aws/credentials   # paste the real keys here
```

The AWS CLI reads `~/.aws/credentials` automatically; nothing else
to configure.

**Option B — environment variables (preferred for CI / one-off shells)**

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=eu-north-1
# Optional, only if your IAM identity requires session tokens:
# export AWS_SESSION_TOKEN=...
```

Both options are equivalent for the deploy script — pick whichever
fits the host you're running on. **Never paste these values into
chat, the issue tracker, or a tracked file in this repo.**

### 3. Verify before deploying

```bash
aws sts get-caller-identity
# → { "Account": "467685081786", "Arn": "arn:aws:iam::467685081786:user/kw-explorer-deploy", ... }
```

If that prints the right account / user, the deploy script will
work. If it errors with `Unable to locate credentials`, fix step 2
before continuing.

### 4. Rotation

Rotate access keys quarterly (or sooner if a developer leaves the
project). `aws iam create-access-key` → update `~/.aws/credentials`
→ `aws iam delete-access-key` for the old one.

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
