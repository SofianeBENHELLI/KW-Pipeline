"""Scope schema (ADR-020 §1).

Workspace scoping uses three flavors — ``personal``, ``swym_community``,
``project`` — and a join table that lets a single document live in N
scopes simultaneously. This module defines the wire-shape Pydantic
models that the catalog persists, the upload route reads, and the
follow-up read-side filter (D.5) will return.

The shapes here are intentionally minimal: only the fields the
``document_scopes`` table carries (``kind`` / ``ref`` / ``added_at`` /
``added_by``). The richer ``Scope`` registry shape from ADR-020 (label,
created_by, etc.) lives in a separate scope-registry slice — this PR
only covers the **document → scope link**.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas import APISchemaModel as BaseModel

# The three scope flavors. Stored as a plain :class:`Literal` (not a
# :class:`StrEnum`) so the catalog persists raw strings and clients
# pattern-match on them without importing a Python enum. The tuple of
# allowed values matches ADR-020 §1 verbatim — adding a new flavor is
# a schema migration, not a default tweak.
ScopeKind = Literal["personal", "swym_community", "project"]

# Ordered tuple used for runtime validation when the route layer takes
# a free-form string (query param) and needs to coerce it into the
# Literal. Keeping the tuple alongside the Literal avoids the typing
# trick of pulling ``__args__`` off the Literal at runtime.
SCOPE_KINDS: tuple[ScopeKind, ...] = ("personal", "swym_community", "project")


class ScopeRef(BaseModel):
    """The ``(kind, ref)`` pair that identifies one scope.

    Used as a payload shape on requests that accept a scope filter
    without the audit metadata — e.g. the future read-side filter
    (D.5). The full :class:`Scope` shape is the response-side type
    that the catalog returns alongside its ``added_at`` / ``added_by``
    audit columns.
    """

    kind: ScopeKind
    ref: str


class Scope(BaseModel):
    """One row of the ``document_scopes`` join table.

    Returned by :meth:`CatalogStore.list_scopes_for_document` and by
    the upload-route response when a scope link was created. The pair
    ``(kind, ref)`` identifies the scope; ``added_at`` / ``added_by``
    record who linked the document into that scope and when.

    The ``ref`` field is intentionally opaque to this layer: its
    interpretation depends on ``kind`` (a 3DSwym community id, a
    user id, an internal project id) and is owned by ADR-026 / the
    membership client. The catalog persists it as a flat string.
    """

    kind: ScopeKind
    ref: str
    added_at: datetime
    added_by: str


__all__ = ["SCOPE_KINDS", "Scope", "ScopeKind", "ScopeRef"]
