"""Shared helpers used by every sub-router.

Lives here so each sub-router (``upload``, ``lifecycle``, ``knowledge``,
``admin``) can ``from app.routes._helpers import ...`` without
introducing a circular import or duplicating the idempotency /
settings plumbing.

The names exported here are intentionally a small, stable surface:

- :class:`ReviewRequest` — the validate / reject body model.
- :func:`_request_settings` — per-request :class:`Settings` factory so
  ``monkeypatch.setenv`` shows up immediately.
- :func:`_check_idempotency` / :func:`_store_idempotency` — the cache
  read / write half of the Idempotency-Key contract documented in
  ``docs/architecture/api_contract.md``.
- Pagination + upload-streaming constants used across multiple routes.
"""

from __future__ import annotations

import json
import logging

from fastapi import Response
from pydantic import BaseModel

from app.errors import ApiError, ErrorCode
from app.services.idempotency_store import IdempotencyStore
from app.settings import Settings

log = logging.getLogger(__name__)

# Cursor pagination guardrails for ``GET /documents``. The default page
# size matches the in-memory store's typical working set; the max
# ceiling keeps a single response under a few hundred KB even with
# verbose versions.
DEFAULT_PAGE_LIMIT = 50
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 200

# Streaming read granularity for the upload route. Matches the storage
# service's write granularity so peak resident memory during upload is
# one chunk plus framing overhead, regardless of total payload size.
UPLOAD_READ_CHUNK_SIZE = 8 * 1024 * 1024
# Threshold below which ``SpooledTemporaryFile`` keeps bytes in RAM.
# Chosen at 1 MiB so anything larger spills to a real file on disk;
# this keeps the resident set bounded for multi-GB uploads while still
# avoiding a syscall round-trip for small ones.
SPOOL_ROLLOVER_BYTES = 1 * 1024 * 1024

# Knowledge-graph page floor. The ceiling lives in
# ``app.services.knowledge.graph_store.MAX_GRAPH_PAGE_LIMIT``; we only
# need the floor here for the ``GET /knowledge/graph`` Query default.
MIN_GRAPH_PAGE_LIMIT = 1


def _request_settings() -> Settings:
    """Construct a fresh :class:`Settings` for one request.

    Settings are read per-request rather than cached at app startup so
    a test that calls ``monkeypatch.setenv("MAX_UPLOAD_BYTES", ...)``
    and issues a request immediately afterwards observes the new
    value. Pydantic Settings construction is cheap (no I/O, just an
    env-var walk), so the overhead is negligible compared with the
    work the upload route already does per call.
    """
    return Settings()


class ReviewRequest(BaseModel):
    """Optional reviewer note attached to a validate or reject decision."""

    reviewer_note: str | None = None


def _check_idempotency(
    *,
    store: IdempotencyStore,
    idempotency_key: str | None,
    route: str,
    request_hash: str,
) -> Response | None:
    """Check the idempotency store for a cached response.

    Returns a :class:`Response` if the request is a replay (caller
    should return it directly), or ``None`` if the request should
    proceed normally.

    Raises ``ApiError(422)`` when the key is reused with a different
    request body.
    """
    if idempotency_key is None:
        return None

    stored = store.get(idempotency_key, route)
    if stored is None:
        return None

    if stored.request_hash != request_hash:
        raise ApiError(
            status_code=422,
            code=ErrorCode.IDEMPOTENCY_REPLAY,
            message="Idempotency-Key reused with different request body",
            retryable=False,
            remediation=(
                "Pick a fresh Idempotency-Key for the new request, or "
                "re-send exactly the same body to replay the cached "
                "response."
            ),
        )

    log.info(
        "idempotency.replayed",
        extra={
            "route": route,
            "idempotency_key": idempotency_key,
            "response_status": stored.response_status,
        },
    )
    # Return the cached response byte-identical to the original.
    return Response(
        content=stored.response_json,
        status_code=stored.response_status,
        media_type="application/json",
    )


def _store_idempotency(
    *,
    store: IdempotencyStore,
    idempotency_key: str | None,
    route: str,
    request_hash: str,
    result: object,
) -> None:
    """Persist a successful response in the idempotency store if a key is present."""
    if idempotency_key is None:
        return
    store.put(
        key=idempotency_key,
        route=route,
        request_hash=request_hash,
        response_status=200,
        response_json=json.dumps(result, default=str),
    )
