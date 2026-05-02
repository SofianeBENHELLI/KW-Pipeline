# API Contract

## Error envelope (#97 / #120)

Every non-2xx response carries the same shape:

```json
{
  "error": {
    "code": "KW_UPLOAD_EMPTY",
    "message": "Uploaded file is empty.",
    "status": 400,
    "retryable": false,
    "remediation": "Pick a file that has content and re-upload. The byte stream we received was zero-length."
  },
  "detail": "Uploaded file is empty."
}
```

| Field | Meaning |
|---|---|
| `error.code` | Stable machine-readable identifier. KW-Pipeline-specific codes are prefixed `KW_`. Generic fallbacks (`KW_NOT_FOUND`, `KW_HTTP_ERROR`) come from the HTTP status when a raise site doesn't pick a more specific one. |
| `error.message` | Short user-facing summary. |
| `error.status` | HTTP status (mirrors the response status — convenient for clients that read the body without inspecting headers). |
| `error.retryable` | `true` iff the same request might succeed if retried (transient backend, rate-limit). `false` for permanent errors. Frontends use this to decide whether to surface a Retry button. |
| `error.remediation` | Optional actionable hint (`null` when none applies). Frontends render this in their notice banners. |
| `detail` | Legacy field preserved alongside the envelope for older clients that read FastAPI's default error shape (issue #120). |

### Code catalog

| Code | Status | Retryable | Where raised |
|---|---|---|---|
| `KW_UPLOAD_EMPTY` | 400 | false | `POST /documents/upload` — body has zero bytes. |
| `KW_UPLOAD_TOO_LARGE` | 413 | false | `POST /documents/upload` — body exceeds `MAX_UPLOAD_BYTES`. |
| `KW_UPLOAD_UNSUPPORTED_TYPE` | 415 | false | `POST /documents/upload` — content type not in `KW_ALLOWED_CONTENT_TYPES`. |
| `KW_LIFECYCLE_CONFLICT` | 409 | false | `POST /documents/{id}/versions/{vid}/{validate,reject,extract,semantic}` — version's lifecycle status doesn't permit the transition. |
| `KW_IDEMPOTENCY_REPLAY` | 422 | false | Any POST with an `Idempotency-Key` header, when the key was previously used with a different request body. |
| `KW_VALIDATION_ERROR` | 422 | false | FastAPI/Pydantic request-validation failures (malformed query params, body schema mismatches). |
| `KW_NOT_FOUND` | 404 | false | Generic fallback for `HTTPException(status_code=404)` raises (e.g. unknown document/version/extraction). |
| `KW_CONFLICT` | 409 | false | Generic fallback for `HTTPException(status_code=409)` raises. |
| `KW_BAD_REQUEST`, `KW_UNAUTHORIZED`, `KW_FORBIDDEN`, `KW_PAYLOAD_TOO_LARGE`, `KW_UNSUPPORTED_MEDIA_TYPE`, `KW_UNPROCESSABLE_ENTITY`, `KW_HTTP_ERROR` | various | false | Status-derived fallbacks for the rest. |

Adding a new code is a public-API change: define it in [`apps/api/app/errors.py`](../../apps/api/app/errors.py) (`ErrorCode` class), document it here, and add a regression test to [`apps/api/tests/test_error_contract.py`](../../apps/api/tests/test_error_contract.py) that pins the (status, code, retryable, remediation) tuple.

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

`GET /documents?limit=50&cursor=<opaque>`

Returns one cursor-paginated page of catalog entries with latest version
metadata.

Response shape:

```json
{
  "items": [Document, ...],
  "next_cursor": "<opaque base64>" | null
}
```

### Pagination

- `limit` defaults to `50`. Valid range: `1 <= limit <= 200`. Values outside
  that range are rejected with HTTP `400 Bad Request` and a detail of
  `"limit must be between 1 and 200; got <N>."`.
- `cursor` is an opaque URL-safe base64 token. Clients MUST treat it as
  opaque — its internal shape (currently base64-of-JSON over
  `[created_at_iso, document_id]`) is not part of the public contract and
  may change without notice.
- The cursor encodes the `(created_at, id)` of the **last returned row**,
  so the next page returns rows strictly greater than that tuple under the
  stable ordering `(created_at ASC, id ASC)`. The `id` tie-breaker keeps
  two same-second uploads from shifting between pages.
- `next_cursor` is `null` when the page wasn't full (fewer than `limit`
  rows returned). In that case there is no further data to walk. When the
  page is exactly full, `next_cursor` is non-null and a follow-up call may
  yield an empty page (`{"items": [], "next_cursor": null}`).
- A malformed `cursor` (bad base64, malformed JSON, wrong shape, wrong
  types, unparseable datetime) is rejected with HTTP `400 Bad Request` and
  a detail of `"Invalid cursor: <reason>"`. The route never returns 500
  for client-supplied cursor errors.

### Backward compatibility

The previous shape (a bare JSON array of documents) is intentionally
broken. Callers that previously did `response.json()` to receive a list
must now read `response.json()["items"]`. There is no transitional
fallback — fielding two response shapes is worse than the cutover.

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
