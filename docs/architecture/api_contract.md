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

Returns extraction status, parser metadata, raw extraction availability, and
failure details.

## Generate Semantic Output

`POST /documents/{document_id}/versions/{version_id}/semantic`

Generates semantic JSON and Markdown from raw extraction JSON.

## Get Semantic Output

`GET /documents/{document_id}/versions/{version_id}/semantic`

Returns semantic JSON metadata, warnings, Markdown availability, and validation
status.

## Review Semantic Output

`POST /documents/{document_id}/versions/{version_id}/review`

Accepts:

- `decision`: `validated` or `rejected`
- `reviewer`
- `notes`
