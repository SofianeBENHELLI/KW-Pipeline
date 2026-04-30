# API Contract

## Upload Document

`POST /documents/upload`

Accepts multipart file upload and returns document metadata.

Response fields:

- `document_id`
- `version_id`
- `filename`
- `content_type`
- `file_size`
- `sha256`
- `status`
- `duplicate_of_version_id`

### Upload size limit

Uploads larger than `MAX_UPLOAD_BYTES` are rejected with HTTP `413 Payload Too
Large` and a detail of `"Upload exceeds limit of <N> bytes"`.

`MAX_UPLOAD_BYTES` is read from the environment at request time. When unset it
defaults to `52428800` (50 MiB). Streaming enforcement (rejecting before the
whole body is buffered) is tracked separately in #41.

### Content type allowlist

The request `Content-Type` of the uploaded part is compared against
`ALLOWED_CONTENT_TYPES`, a comma-separated list read from the environment at
request time. When unset it defaults to `text/plain`. PDF and DOCX entries
will be added once their parsers land in milestone 4.

Media-type parameters are stripped before comparison, so
`text/plain; charset=utf-8` is accepted when `text/plain` is on the allowlist.

A disallowed content type produces HTTP `415 Unsupported Media Type` with a
detail of `"Content type '<received>' is not allowed. Allowed: <sorted, joined>"`.

## List Documents

`GET /documents`

Returns catalog entries with latest version metadata.

## Get Document

`GET /documents/{document_id}`

Returns the document, its versions, current lifecycle state, and output
availability.

## Queue Extraction

`POST /documents/{document_id}/versions/{version_id}/extract`

Starts raw extraction for a stored document version.

## Get Extraction

`GET /documents/{document_id}/versions/{version_id}/extraction`

Returns cached raw extraction JSON for a document version.

Returns `404` when extraction has not run or the document version does not
exist.

## Generate Semantic Output

`POST /documents/{document_id}/versions/{version_id}/semantic`

Generates semantic JSON and Markdown from cached raw extraction JSON. Repeated
calls return the cached semantic output instead of regenerating it.

## Get Semantic Output

`GET /documents/{document_id}/versions/{version_id}/semantic`

Returns cached semantic JSON, including warnings, source references, validation
status, and generated Markdown when available.

Returns `404` when semantic output has not been generated.

## Get Markdown Output

`GET /documents/{document_id}/versions/{version_id}/markdown`

Returns cached generated Markdown as `text/markdown`.

Returns `404` when Markdown output has not been generated.

## Review Semantic Output

`POST /documents/{document_id}/versions/{version_id}/review`

Accepts:

- `decision`: `validated` or `rejected`
- `reviewer`
- `notes`

## CORS

The API installs Starlette's `CORSMiddleware` so the Orbital frontend can talk
to it from a separate origin. The allowlist is read from the
`CORS_ALLOWED_ORIGINS` environment variable as a comma-separated list of exact
origins (no wildcards):

```
CORS_ALLOWED_ORIGINS=http://localhost:5173,https://orbital.example.com
```

Behaviour:

- `allow_origins` — exactly the origins parsed from the env var. Empty by
  default, which means no cross-origin requests are accepted until an operator
  opts in.
- `allow_credentials` — `False` (cookies and `Authorization` are not echoed).
- `allow_methods` — `GET`, `POST`, `OPTIONS`.
- `allow_headers` — any (frontend can send `Content-Type`, etc.).

Preflight `OPTIONS` requests from an origin in the allowlist receive a
matching `Access-Control-Allow-Origin` header; requests from origins outside
the allowlist do not. The env-var read is intentionally inline; it will be
folded into Pydantic Settings once issue #43 lands.
