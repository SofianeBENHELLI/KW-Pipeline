"""Operator-imposed taxonomy loader (ADR-017 §3 + §5).

ADR-017 picks YAML committed to the repo as the v1 source of edits:
reproducible, versionable via git, zero auth surface needed for the
foundation slice. The admin HTTP route + KnowledgeForge UI are
deferred with the auth story (#83).

The loader's contract:

- ``load_taxonomy(path)`` reads the YAML at ``path`` and returns a
  parsed :class:`Taxonomy`. Validates id format, id uniqueness,
  and nesting depth before returning. A malformed file raises
  :class:`TaxonomyLoadError` so the operator notices at startup
  rather than serving a silently-broken classifier later.
- ``path is None`` (no path configured) returns ``None``. The
  caller treats this as "taxonomy not configured" and falls back
  to the auto-deduced topic clustering (ADR-017 §1).
- ``path`` set but the file does not exist also returns ``None``
  with a warning logged. Treats a stale ``KW_TAXONOMY_PATH`` env
  var as "not configured" rather than a hard error so a fresh
  deployment can boot before the operator authors the YAML.

Strict validation rules (`MAX_TAXONOMY_DEPTH`, `MAX_TAXONOMY_FANOUT`,
ids match :data:`_VALID_ID_PATTERN`, no duplicate ids across the
tree) all fail fast — the operator is the only audience for these
errors and they want to fix the YAML, not paper over it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.schemas.taxonomy import (
    MAX_TAXONOMY_DEPTH,
    TAXONOMY_SCHEMA_VERSION,
    Taxonomy,
    TaxonomyCategory,
)

log = logging.getLogger(__name__)

# Category ids: lower-snake plus dot-separator for hierarchy
# (``hr.hybrid_work.cross_border``). Hyphens are allowed for natural
# multi-word labels (``ai-act``). The pattern rejects whitespace,
# unicode, and uppercase so ids stay URL-safe and SQL-safe.
_VALID_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,199}$")


class TaxonomyLoadError(Exception):
    """Raised when the YAML payload is malformed or violates ADR-017 invariants."""


def load_taxonomy(path: Path | str | None) -> tuple[Taxonomy | None, Path | None]:
    """Load a taxonomy from disk. Returns ``(taxonomy, resolved_path)``.

    Both halves are ``None`` when no path is configured or the file
    does not exist. The route layer translates this to
    ``TaxonomyResponse(is_configured=False, ...)``.

    Raises :class:`TaxonomyLoadError` on malformed YAML, schema
    mismatch, duplicate ids, illegal id format, or excessive
    depth/fanout. The error message points at the offending category
    so operators can fix their YAML without spelunking.
    """
    if path is None or (isinstance(path, str) and not path.strip()):
        return None, None
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        log.warning(
            "knowledge.taxonomy.path_missing",
            extra={"taxonomy_path": str(resolved)},
        )
        return None, resolved
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem permission edge
        raise TaxonomyLoadError(f"Failed to read taxonomy file at {resolved}: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TaxonomyLoadError(f"Taxonomy file at {resolved} is not valid YAML: {exc}") from exc
    if document is None:
        # Empty file → treat as configured-but-empty, mirrors the
        # YAML semantics where ``null`` is a valid root.
        return Taxonomy(categories=[]), resolved

    taxonomy = _parse_root(document, source=str(resolved))
    _validate_invariants(taxonomy, source=str(resolved))
    log.info(
        "knowledge.taxonomy.loaded",
        extra={
            "taxonomy_path": str(resolved),
            "category_count": _count_categories(taxonomy),
            "schema_version": taxonomy.schema_version,
        },
    )
    return taxonomy, resolved


def _parse_root(document: Any, *, source: str) -> Taxonomy:
    """Coerce the YAML document into a :class:`Taxonomy`."""
    if not isinstance(document, dict):
        raise TaxonomyLoadError(
            f"Taxonomy at {source} must be a mapping at the root; got {type(document).__name__}"
        )
    body = document.get("taxonomy")
    if body is None:
        # Tolerate a flat root ({categories: ..., schema_version: ...})
        # in addition to the documented nested ({taxonomy: {...}}).
        # Both shapes appear in the ADR's example and operator drafts;
        # accepting both keeps the contract forgiving without breaking
        # validation downstream.
        body = document
    if not isinstance(body, dict):
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: expected a `taxonomy` mapping, got {type(body).__name__}"
        )
    schema_version = body.get("schema_version", TAXONOMY_SCHEMA_VERSION)
    if schema_version != TAXONOMY_SCHEMA_VERSION:
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: schema_version "
            f"{schema_version!r} is not supported; "
            f"this build understands {TAXONOMY_SCHEMA_VERSION!r}"
        )
    raw_categories = body.get("categories", [])
    if not isinstance(raw_categories, list):
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: `categories` must be a list; "
            f"got {type(raw_categories).__name__}"
        )
    categories = [
        _parse_category(c, path=str(i), source=source) for i, c in enumerate(raw_categories)
    ]
    try:
        return Taxonomy(categories=categories)
    except Exception as exc:  # noqa: BLE001 - re-raised as TaxonomyLoadError
        raise TaxonomyLoadError(f"Taxonomy at {source}: {exc}") from exc


def _parse_category(raw: Any, *, path: str, source: str) -> TaxonomyCategory:
    if not isinstance(raw, dict):
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category at path {path} must be a mapping; "
            f"got {type(raw).__name__}"
        )
    cid = raw.get("id")
    label = raw.get("label")
    description = raw.get("description")
    # ``subcategories:`` with no value parses as ``None`` in YAML;
    # treat that as an empty list so authors can write the key
    # without a body when they intend "no children".
    sub = raw.get("subcategories", []) or []
    if not isinstance(cid, str):
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category at path {path} is missing a string `id`."
        )
    if not _VALID_ID_PATTERN.match(cid):
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category id {cid!r} (path {path}) "
            f"must match {_VALID_ID_PATTERN.pattern!r}."
        )
    if not isinstance(label, str) or not label.strip():
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category {cid!r} (path {path}) is missing a non-empty `label`."
        )
    if not isinstance(description, str) or not description.strip():
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category {cid!r} (path {path}) "
            f"is missing a non-empty `description` — the classifier "
            f"reads this to assign chunks to the category."
        )
    if not isinstance(sub, list):
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category {cid!r} (path {path}) "
            f"`subcategories` must be a list; got {type(sub).__name__}."
        )
    subcategories = [
        _parse_category(s, path=f"{path}/{i}", source=source) for i, s in enumerate(sub)
    ]
    try:
        return TaxonomyCategory(
            id=cid,
            label=label,
            description=description,
            subcategories=subcategories,
            # Operator-authored YAML always emits ``source="imposed"``.
            # The default on the schema is also ``"imposed"`` so this
            # is mostly defensive — the route layer relies on the
            # explicit tag when merging with computed clusters (#249).
            source="imposed",
        )
    except Exception as exc:  # noqa: BLE001
        raise TaxonomyLoadError(
            f"Taxonomy at {source}: category {cid!r} (path {path}): {exc}"
        ) from exc


def _validate_invariants(taxonomy: Taxonomy, *, source: str) -> None:
    """Enforce ADR-017's structural invariants across the parsed tree."""
    seen_ids: set[str] = set()

    def walk(category: TaxonomyCategory, depth: int) -> None:
        if depth > MAX_TAXONOMY_DEPTH:
            raise TaxonomyLoadError(
                f"Taxonomy at {source}: category {category.id!r} "
                f"exceeds maximum nesting depth ({MAX_TAXONOMY_DEPTH})."
            )
        if category.id in seen_ids:
            raise TaxonomyLoadError(
                f"Taxonomy at {source}: duplicate category id "
                f"{category.id!r} — ids must be unique across the tree."
            )
        seen_ids.add(category.id)
        for child in category.subcategories:
            walk(child, depth + 1)

    for category in taxonomy.categories:
        walk(category, depth=1)


def _count_categories(taxonomy: Taxonomy) -> int:
    """Count nodes across the tree — used for the structured-log payload."""

    def walk(category: TaxonomyCategory) -> int:
        return 1 + sum(walk(c) for c in category.subcategories)

    return sum(walk(c) for c in taxonomy.categories)


__all__ = ["TaxonomyLoadError", "load_taxonomy"]
