"""Upload routes — single and batch.

Both endpoints stream their payloads through a ``SpooledTemporaryFile``
so peak resident memory is bounded by ``UPLOAD_READ_CHUNK_SIZE``
regardless of the file size. The batch route never raises on a
per-file error: failures land in the response body so a single bad
file doesn't abort the whole batch.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.document import (
    BatchUploadOutcome,
    BatchUploadResult,
    BatchUploadSummary,
    UploadDocumentResponse,
)
from app.schemas.scope import SCOPE_KINDS, Scope, ScopeKind
from app.services.auth import User, require_contributor

from ._helpers import (
    SPOOL_ROLLOVER_BYTES,
    UPLOAD_READ_CHUNK_SIZE,
    _check_idempotency,
    _request_settings,
    _store_idempotency,
)

log = logging.getLogger(__name__)


def _resolve_upload_scope(
    *,
    scope_kind: str | None,
    scope_ref: str | None,
    current_user: User,
) -> tuple[ScopeKind, str]:
    """Pick the scope link for a single upload (EPIC-D D.1, ADR-020 §1).

    Both query params must be set or neither: a partial pair is a 422
    so the client can't land an upload in personal:<user_id> when it
    thought it targeted a community. When both are absent, the upload
    defaults to the caller's personal scope using the auth-resolved
    ``current_user.id`` (``"dev"`` in dev mode, ``"anonymous"`` under
    ``KW_AUTH_MODE=disabled``).
    """
    has_kind = scope_kind is not None and scope_kind != ""
    has_ref = scope_ref is not None and scope_ref != ""
    if has_kind != has_ref:
        # FastAPI surfaces this as HTTP 422 via ApiError because the
        # client supplied a half-pair — the route layer rejects it
        # before any catalog work happens.
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="scope_kind and scope_ref must be provided together.",
            retryable=False,
            remediation=(
                "Send both scope_kind and scope_ref query params, or "
                "neither (the upload defaults to the caller's personal "
                "scope when both are omitted)."
            ),
        )
    if not has_kind:
        # Default to ``personal:<user_id>`` for the auth-resolved
        # principal. Matches ADR-020 §1: every user has a personal
        # scope from day one and it's the default upload destination.
        return "personal", current_user.id
    assert scope_kind is not None and scope_ref is not None  # for mypy
    if scope_kind not in SCOPE_KINDS:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message=(f"Invalid scope_kind '{scope_kind}'. Allowed: {', '.join(SCOPE_KINDS)}."),
            retryable=False,
            remediation=(
                "Pick one of the documented scope kinds: 'personal', "
                "'swym_community', or 'project'. See ADR-020 §1."
            ),
        )
    return scope_kind, scope_ref  # type: ignore[return-value]


def build_upload_router(services: PipelineServices) -> APIRouter:
    """Register single + batch upload routes."""
    router = APIRouter()

    @router.post(
        "/documents/upload",
        operation_id="upload_document",
        response_model=UploadDocumentResponse,
    )
    async def upload_document(
        file: UploadFile = File(...),
        document_id: str | None = None,
        scope_kind: str | None = None,
        scope_ref: str | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: User = Depends(require_contributor),
    ) -> Any:
        settings = _request_settings()
        max_bytes = settings.max_upload_bytes
        allowed = settings.allowed_content_types

        # Resolve the scope link for this upload (EPIC-D D.1, ADR-020 §1).
        # Either both query params must be set or neither — a half-set
        # pair is a 422 so the client can't accidentally land an upload
        # in personal:<user_id> when they thought they targeted a
        # community.
        resolved_kind, resolved_ref = _resolve_upload_scope(
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            current_user=current_user,
        )

        # Strip any media-type parameters (e.g. ``; charset=utf-8``) before
        # comparing against the allowlist — RFC 7231 lets clients tack them
        # on freely, but the bare type is what we gate on.
        raw_content_type = file.content_type or "application/octet-stream"
        bare_content_type = raw_content_type.split(";")[0].strip()
        if bare_content_type not in allowed:
            allowed_list = ", ".join(sorted(allowed))
            raise ApiError(
                status_code=415,
                code=ErrorCode.UPLOAD_UNSUPPORTED_TYPE,
                message=(
                    f"Content type '{bare_content_type}' is not allowed. Allowed: {allowed_list}"
                ),
                retryable=False,
                remediation=(
                    "Re-upload the file with one of the allowed content "
                    "types, or ask an operator to widen the "
                    "KW_ALLOWED_CONTENT_TYPES allowlist."
                ),
            )

        # Spool the upload to a temp file in 8 MiB chunks so peak resident
        # memory tracks the chunk size, not the payload size. The size limit
        # is enforced incrementally — we stop reading the moment the running
        # total crosses ``max_bytes``, so a 51 MB body never materialises.
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_ROLLOVER_BYTES, mode="w+b") as spool:
            total = 0
            # Hash chunks as they stream in so the request fingerprint costs
            # nothing beyond the existing read loop — reading the spool back
            # into a ``bytes`` would defeat the streaming-memory budget.
            hasher = hashlib.sha256() if idempotency_key else None
            while True:
                chunk = await file.read(UPLOAD_READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ApiError(
                        status_code=413,
                        code=ErrorCode.UPLOAD_TOO_LARGE,
                        message=f"Upload exceeds limit of {max_bytes} bytes",
                        retryable=False,
                        remediation=(
                            "Compress the file or split it into smaller "
                            "pieces before re-uploading. The current "
                            f"limit is {max_bytes} bytes (configurable via "
                            "MAX_UPLOAD_BYTES)."
                        ),
                    )
                if hasher is not None:
                    hasher.update(chunk)
                spool.write(chunk)
            if total == 0:
                raise ApiError(
                    status_code=400,
                    code=ErrorCode.UPLOAD_EMPTY,
                    message="Uploaded file is empty.",
                    retryable=False,
                    remediation=(
                        "Pick a file that has content and re-upload. The "
                        "byte stream we received was zero-length."
                    ),
                )
            spool.seek(0)

            _route = "/documents/upload"
            _req_hash = hasher.hexdigest() if hasher is not None else ""
            cached = _check_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
            )
            if cached is not None:
                return cached

            def _iter_chunks() -> Iterator[bytes]:
                while True:
                    block = spool.read(UPLOAD_READ_CHUNK_SIZE)
                    if not block:
                        return
                    yield block

            try:
                version = services.documents.upload_stream(
                    filename=file.filename or "untitled",
                    content_type=raw_content_type,
                    chunks=_iter_chunks(),
                    document_id=document_id,
                )
                # Persist the scope link before returning. ``add_scope`` is
                # idempotent on (document_id, kind, ref) so the same upload
                # replayed under a different idempotency key (or a duplicate
                # detection that landed on an existing family) doesn't
                # double-record. The catalog row's ``added_at`` is the wall
                # clock when the link was created — the version's own
                # ``created_at`` is preserved separately.
                scope = Scope(
                    kind=resolved_kind,
                    ref=resolved_ref,
                    added_at=datetime.now(UTC),
                    added_by=current_user.id,
                )
                services.documents.catalog.add_scope(version.document_id, scope)
                # Return the full :class:`UploadDocumentResponse` so the
                # client sees every scope this document is linked to —
                # including any scopes recorded by previous uploads of
                # the same family (e.g. duplicate detection or explicit
                # ``document_id`` re-upload). Avoids a second round-trip
                # to ``list_scopes_for_document``.
                scopes = services.documents.catalog.list_scopes_for_document(version.document_id)
                result = UploadDocumentResponse(**version.model_dump(), scopes=scopes)
                _store_idempotency(
                    store=services.idempotency,
                    idempotency_key=idempotency_key,
                    route=_route,
                    request_hash=_req_hash,
                    result=result.model_dump(mode="json"),
                )
                return result
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/documents/upload/batch",
        operation_id="upload_documents_batch",
        response_model=BatchUploadResult,
    )
    async def upload_documents_batch(
        files: list[UploadFile] = File(...),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: User = Depends(require_contributor),  # noqa: ARG001 — gate-only dep, body uses no actor today
    ) -> Any:
        """Bulk upload — one request, one structured per-file report (#82).

        Per-file outcomes never raise: a file that fails MIME validation,
        the size cap, the empty-body check, or a downstream error lands
        in the response body with a populated ``error_code`` /
        ``error_message`` pair. The route returns 200 even when every
        file failed; clients route on the ``summary`` counters.

        Idempotency-Key replay returns the original report unchanged. A
        zero-file body returns 400 (the only kind of "request envelope"
        error this route raises).
        """
        if not files:
            raise ApiError(
                status_code=400,
                code=ErrorCode.UPLOAD_EMPTY,
                message="No files attached. Include at least one file part.",
                retryable=False,
                remediation=(
                    "Send a multipart/form-data body with one or more "
                    "`files` parts; each part is one file to ingest."
                ),
            )

        settings = _request_settings()
        max_bytes = settings.max_upload_bytes
        allowed = settings.allowed_content_types

        # Hash the request envelope across every file so the
        # idempotency cache returns the same report on replay. Per-file
        # hashes are folded in below.
        envelope_hasher = hashlib.sha256() if idempotency_key else None

        # Buffered stage: per file, either classify upfront (MIME /
        # size / empty failures) OR spool the bytes for a later catalog
        # write. ``outcomes`` is sized to ``len(files)`` upfront so each
        # file's slot is independent of when its outcome is computed —
        # the response preserves the input order even though catalog
        # writes happen in a second loop.
        outcomes: list[BatchUploadOutcome | None] = [None] * len(files)
        # Each entry is (input_index, spool, filename, raw_content_type,
        # bare_content_type, total_bytes) for files that need a catalog
        # write. We close every spool in the ``finally`` block.
        pending_writes: list[tuple[int, Any, str, str, str, int]] = []

        try:
            for index, file in enumerate(files):
                raw_content_type = file.content_type or "application/octet-stream"
                bare = raw_content_type.split(";")[0].strip()
                filename = file.filename or "untitled"

                if envelope_hasher is not None:
                    envelope_hasher.update(filename.encode("utf-8"))
                    envelope_hasher.update(b"\x00")
                    envelope_hasher.update(raw_content_type.encode("utf-8"))
                    envelope_hasher.update(b"\x00")

                if bare not in allowed:
                    outcomes[index] = BatchUploadOutcome(
                        filename=filename,
                        content_type=bare,
                        bytes=0,
                        status="rejected_content_type",
                        error_code=ErrorCode.UPLOAD_UNSUPPORTED_TYPE,
                        error_message=(
                            f"Content type '{bare}' is not allowed. "
                            f"Allowed: {', '.join(sorted(allowed))}."
                        ),
                    )
                    continue

                # ruff SIM115 wants a ``with`` block here, but the spool's
                # lifetime spans the loop iteration plus the second
                # ``upload_stream`` pass below. The outer ``finally``
                # closes every spool we created.
                spool = tempfile.SpooledTemporaryFile(  # noqa: SIM115
                    max_size=SPOOL_ROLLOVER_BYTES, mode="w+b"
                )
                total = 0
                too_large = False
                while True:
                    chunk = await file.read(UPLOAD_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        too_large = True
                        break
                    if envelope_hasher is not None:
                        envelope_hasher.update(chunk)
                    spool.write(chunk)

                if too_large:
                    spool.close()
                    outcomes[index] = BatchUploadOutcome(
                        filename=filename,
                        content_type=bare,
                        bytes=total,
                        status="too_large",
                        error_code=ErrorCode.UPLOAD_TOO_LARGE,
                        error_message=f"Upload exceeds limit of {max_bytes} bytes.",
                    )
                    continue
                if total == 0:
                    spool.close()
                    outcomes[index] = BatchUploadOutcome(
                        filename=filename,
                        content_type=bare,
                        bytes=0,
                        status="empty",
                        error_code=ErrorCode.UPLOAD_EMPTY,
                        error_message="Uploaded file is empty.",
                    )
                    continue

                spool.seek(0)
                pending_writes.append((index, spool, filename, raw_content_type, bare, total))

            # Idempotency cache check happens after we've drained every
            # file — the request hash incorporates filenames, MIMEs, and
            # body bytes, so a replay returns the cached report without
            # re-running any catalog writes.
            _route = "/documents/upload/batch"
            _req_hash = envelope_hasher.hexdigest() if envelope_hasher is not None else ""
            cached = _check_idempotency(
                store=services.idempotency,
                idempotency_key=idempotency_key,
                route=_route,
                request_hash=_req_hash,
            )
            if cached is not None:
                return cached

            for index, spool, filename, raw_content_type, bare, total in pending_writes:

                def _iter_chunks(sp: Any = spool) -> Iterator[bytes]:
                    sp.seek(0)
                    while True:
                        block = sp.read(UPLOAD_READ_CHUNK_SIZE)
                        if not block:
                            return
                        yield block

                try:
                    version = services.documents.upload_stream(
                        filename=filename,
                        content_type=raw_content_type,
                        chunks=_iter_chunks(),
                    )
                except Exception as exc:  # noqa: BLE001 - one bad file mustn't abort the batch
                    outcomes[index] = BatchUploadOutcome(
                        filename=filename,
                        content_type=bare,
                        bytes=total,
                        status="failed",
                        error_code="KW_UPLOAD_FAILED",
                        error_message=str(exc),
                    )
                    continue
                is_dup = version.status == DocumentVersionStatus.DUPLICATE_DETECTED
                outcomes[index] = BatchUploadOutcome(
                    filename=filename,
                    content_type=bare,
                    bytes=total,
                    status="duplicate" if is_dup else "uploaded",
                    document_id=version.document_id,
                    version_id=version.id,
                    sha256=version.sha256,
                )
        finally:
            for entry in pending_writes:
                spool = entry[1]
                if spool is not None:
                    # close errors mustn't mask a 500 from the catalog write
                    with contextlib.suppress(Exception):
                        spool.close()

        # Every slot is filled by now — the outer loop classified every
        # file in upfront and the inner loop wrote every pending file.
        # Cast to a non-None list before building the summary.
        materialised: list[BatchUploadOutcome] = [o for o in outcomes if o is not None]

        # Aggregate counters.
        summary = BatchUploadSummary(
            total=len(materialised),
            uploaded=sum(1 for o in materialised if o.status == "uploaded"),
            duplicate=sum(1 for o in materialised if o.status == "duplicate"),
            rejected_content_type=sum(
                1 for o in materialised if o.status == "rejected_content_type"
            ),
            too_large=sum(1 for o in materialised if o.status == "too_large"),
            empty=sum(1 for o in materialised if o.status == "empty"),
            failed=sum(1 for o in materialised if o.status == "failed"),
        )
        result = BatchUploadResult(results=materialised, summary=summary)
        _store_idempotency(
            store=services.idempotency,
            idempotency_key=idempotency_key,
            route=_route,
            request_hash=_req_hash,
            result=result.model_dump(mode="json"),
        )
        return result

    return router
