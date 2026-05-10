"""SQLite-backed storage for the operator-imposed taxonomy
(ADR-017 + ADR-031, #379).

Replaces the YAML-only path that loaded the taxonomy at boot via
:func:`app.services.taxonomy_loader.load_taxonomy`. The YAML loader
stays in place as a **bootstrap import** — admins call
``POST /admin/taxonomy/import_yaml`` (or the lifespan hook does it
automatically when the SQLite store is empty and ``KW_TAXONOMY_PATH``
points at a readable file) to seed the SQLite store from disk.

Two storage shapes:

* :class:`InMemoryTaxonomyStore` for tests and the in-process demo.
* :class:`SQLiteTaxonomyStore` for the persistent runtime.

Both expose the same :class:`TaxonomyStore` Protocol so call sites
(boot wiring, the import endpoint, the read route) don't care which
backend is active.

The wire shape returned by :meth:`get_active` is the existing
:class:`app.schemas.taxonomy.Taxonomy` so nothing downstream changes
when we swap the backend.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.schemas.taxonomy import Taxonomy, TaxonomyCategory
from app.services.taxonomy_loader import TaxonomyLoadError, load_taxonomy

# Recorded on the ``taxonomies.source`` column. The set is closed —
# new sources require both a value here and an audit-event policy
# decision.
TAXONOMY_SOURCE_YAML_IMPORT = "yaml_import"
TAXONOMY_SOURCE_API = "api"

_VALID_SOURCES = frozenset({TAXONOMY_SOURCE_YAML_IMPORT, TAXONOMY_SOURCE_API})


class TaxonomyStore(Protocol):
    """Persistence boundary for the imposed taxonomy."""

    def get_active(self) -> Taxonomy | None:
        """Return the currently-active taxonomy, or ``None`` if no
        taxonomy has ever been published."""

    def publish(
        self,
        taxonomy: Taxonomy,
        *,
        source: str,
        actor: str,
        now: datetime | None = None,
    ) -> str:
        """Persist a new taxonomy version and flip it to active.

        Returns the new ``taxonomy_id`` (server-generated). The
        previous active row, if any, is flipped to ``active=0`` in
        the same transaction so the active set is always size ≤ 1.

        ``source`` must be one of :data:`TAXONOMY_SOURCE_YAML_IMPORT`
        or :data:`TAXONOMY_SOURCE_API`.
        """


class InMemoryTaxonomyStore:
    """In-memory store — ordered list of (id, source, taxonomy)
    tuples; the last entry is active."""

    def __init__(self) -> None:
        self._versions: list[tuple[str, str, Taxonomy]] = []

    def get_active(self) -> Taxonomy | None:
        if not self._versions:
            return None
        return self._versions[-1][2]

    def publish(
        self,
        taxonomy: Taxonomy,
        *,
        source: str,
        actor: str,
        now: datetime | None = None,
    ) -> str:
        del actor, now  # captured for parity with the SQLite store
        if source not in _VALID_SOURCES:
            raise ValueError(f"Unknown taxonomy source: {source!r}")
        new_id = uuid.uuid4().hex
        self._versions.append((new_id, source, taxonomy))
        return new_id


class SQLiteTaxonomyStore:
    """SQLite-backed taxonomy store. Migration ``0010_taxonomy``
    creates the schema this class writes against."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        # Match the catalog store's PRAGMA posture (FK enforcement
        # so the cascade delete on taxonomy_categories actually
        # fires when a taxonomies row is removed).
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def get_active(self) -> Taxonomy | None:
        with self._connect() as connection:
            tax_row = connection.execute(
                "SELECT id, schema_version FROM taxonomies WHERE active = 1 LIMIT 1"
            ).fetchone()
            if tax_row is None:
                return None
            cat_rows = connection.execute(
                """
                SELECT id, parent_id, label, description, sort_order
                FROM taxonomy_categories
                WHERE taxonomy_id = ?
                ORDER BY parent_id IS NULL DESC, parent_id, sort_order
                """,
                (tax_row["id"],),
            ).fetchall()
        return _assemble_tree(cat_rows)

    def publish(
        self,
        taxonomy: Taxonomy,
        *,
        source: str,
        actor: str,
        now: datetime | None = None,
    ) -> str:
        if source not in _VALID_SOURCES:
            raise ValueError(f"Unknown taxonomy source: {source!r}")
        new_id = uuid.uuid4().hex
        when = (now or datetime.now(UTC)).isoformat()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                connection.execute("UPDATE taxonomies SET active = 0 WHERE active = 1")
                connection.execute(
                    """
                    INSERT INTO taxonomies
                        (id, schema_version, source, created_at, created_by, active)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (new_id, taxonomy.schema_version, source, when, actor),
                )
                _insert_categories(connection, new_id, taxonomy.categories)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return new_id


# ─── Internal helpers ───────────────────────────────────────────────


def _insert_categories(
    connection: sqlite3.Connection,
    taxonomy_id: str,
    categories: list[TaxonomyCategory],
    *,
    parent_id: str | None = None,
) -> None:
    for sort_order, category in enumerate(categories):
        connection.execute(
            """
            INSERT INTO taxonomy_categories
                (taxonomy_id, id, parent_id, label, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                taxonomy_id,
                category.id,
                parent_id,
                category.label,
                category.description,
                sort_order,
            ),
        )
        if category.subcategories:
            _insert_categories(
                connection,
                taxonomy_id,
                category.subcategories,
                parent_id=category.id,
            )


def _assemble_tree(rows: list[sqlite3.Row]) -> Taxonomy:
    """Re-build the recursive :class:`Taxonomy` from a flat row set.

    Rows arrive sorted by ``(parent IS NULL DESC, parent_id, sort_order)``
    so the linear pass below sees every parent before any of its
    children — building the tree top-down requires no recursion or
    second pass.
    """
    by_id: dict[str, TaxonomyCategory] = {}
    children_of: dict[str | None, list[TaxonomyCategory]] = {}
    # Two-pass build: first construct each TaxonomyCategory with the
    # final sub-list it should carry; second walk wires the children
    # in. Pydantic's frozen-by-default would otherwise force us to
    # rebuild every node when a child is added.
    parent_by_id: dict[str, str | None] = {}
    for row in rows:
        node = TaxonomyCategory(
            id=row["id"],
            label=row["label"],
            description=row["description"],
            subcategories=[],
            source="imposed",
        )
        by_id[node.id] = node
        parent_by_id[node.id] = row["parent_id"]
        children_of.setdefault(row["parent_id"], []).append(node)
    # Wire children in their stored order.
    for node_id, node in by_id.items():
        sub = children_of.get(node_id, [])
        if sub:
            # TaxonomyCategory is a Pydantic model — assign a fresh
            # list to ``subcategories``. The model_validator on
            # ``TaxonomyCategory`` re-checks fanout but doesn't fight
            # the in-place mutation because the field default is
            # already a mutable list.
            node.subcategories.extend(sub)
    top_level = children_of.get(None, [])
    return Taxonomy(categories=top_level)


def import_yaml_into_store(
    store: TaxonomyStore,
    *,
    yaml_path: Path | str,
    actor: str,
    now: datetime | None = None,
) -> str | None:
    """Bootstrap import: read the YAML at ``yaml_path`` and publish
    it into ``store``. Returns the new taxonomy id, or ``None`` if
    the YAML couldn't be loaded (missing file, empty path).

    Idempotence is the caller's responsibility: every call publishes
    a new taxonomy version. Operators who don't want repeat publishes
    on every redeploy should gate the call (e.g. only invoke when
    ``store.get_active()`` is ``None``).

    Raises :class:`TaxonomyLoadError` on a malformed YAML payload —
    the operator is the only audience for this and they want to fix
    the file rather than have the import silently no-op.
    """
    taxonomy, _resolved = load_taxonomy(yaml_path)
    if taxonomy is None:
        return None
    return store.publish(
        taxonomy,
        source=TAXONOMY_SOURCE_YAML_IMPORT,
        actor=actor,
        now=now,
    )


__all__ = [
    "InMemoryTaxonomyStore",
    "SQLiteTaxonomyStore",
    "TAXONOMY_SOURCE_API",
    "TAXONOMY_SOURCE_YAML_IMPORT",
    "TaxonomyLoadError",
    "TaxonomyStore",
    "import_yaml_into_store",
]
