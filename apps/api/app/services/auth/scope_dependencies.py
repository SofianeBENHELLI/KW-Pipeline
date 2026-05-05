"""FastAPI dependencies that wire :mod:`scope_filter` into routes.

Two dependencies live here:

- :func:`get_caller_scopes` — turns the optional ``scope_kind`` /
  ``scope_ref`` query params into the effective scope set per request.
  Raises HTTP 403 (via :class:`app.errors.ApiError` /
  ``KW_FORBIDDEN``) when the caller asks for a scope they cannot
  reach. Used by paginated list endpoints (``GET /documents``,
  ``GET /knowledge/catalog``) that filter at the SQL level.
- :func:`assert_can_access_document` — a per-document accessibility
  check used on ``/documents/{id}/...`` paths. Raises HTTP 404 (not
  403) when the caller cannot see the document so the API doesn't
  leak its existence.

Both helpers read :class:`Settings` per-request rather than caching at
startup so a test that monkeypatches ``KW_AUTH_MODE`` between requests
sees the new mode without re-wiring the auth service.
"""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from app.schemas.scope import ScopeRef
from app.settings import Settings

from .dependencies import get_current_user
from .protocol import User
from .scope_filter import (
    ScopeAccessDenied,
    resolve_caller_scopes,
    scope_access_denied_to_api_error,
    user_can_access,
)


def get_caller_scopes(
    request: Request,
    scope_kind: str | None = Query(default=None),
    scope_ref: str | None = Query(default=None),
) -> tuple[ScopeRef, ...]:
    """Resolve the caller's effective scope set for this request.

    Returns a tuple of :class:`ScopeRef` instances when the caller
    pinned a real scope (or the default personal scope kicks in).
    Returns the empty-tuple :data:`scope_filter.ALL_SCOPES_SENTINEL`
    when ``KW_AUTH_MODE=disabled`` is in effect — the route then
    skips the predicate entirely.

    Raises :class:`ApiError` (403 / ``KW_FORBIDDEN``) when the
    requested scope is not reachable (cross-user personal,
    pre-D.3 community / project, half-pair, unknown kind).
    """
    user = get_current_user(request)
    settings = Settings()
    try:
        return resolve_caller_scopes(
            user=user,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            settings=settings,
        )
    except ScopeAccessDenied as exc:
        raise scope_access_denied_to_api_error(exc) from exc


def assert_can_access_document(
    request: Request,
    document_id: str,
    user: User,
) -> None:
    """Hide ``document_id`` from ``user`` when the scope filter rejects it.

    Raises HTTP 404 (not 403) per the slice spec — the API must not
    leak the existence of other users' content. The 404 message mirrors
    every other "document not found" surface so an enumeration probe
    sees the same response whether the row is missing or hidden.

    No-op (returns ``None``) when the user passes the filter or when
    ``KW_AUTH_MODE=disabled`` is in effect.
    """
    settings = Settings()
    catalog = request.app.state.services.documents.catalog
    if user_can_access(user=user, document_id=document_id, catalog=catalog, settings=settings):
        return
    # Hidden-existence semantics: 404, not 403. Same detail string
    # ``GET /documents/{id}`` already returns when the row is missing,
    # so the response is byte-identical between "doesn't exist" and
    # "exists but hidden".
    raise HTTPException(status_code=404, detail="Document not found.")


__all__ = [
    "assert_can_access_document",
    "get_caller_scopes",
]
