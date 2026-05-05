"""Flag-only cascade for orphan documents (ADR-020 §4, EPIC-D D.6 + D.7).

When a 3DSwym community is deleted (D.7) — or any other scope-removal
event happens — this service:

1. Soft-removes every ``document_scopes`` row matching the
   ``(scope_kind, scope_ref)`` pair (the ``CatalogStore.remove_scope``
   contract from #262 already preserves the original ``removed_at``
   on a re-flag).
2. For each affected document, re-fetches its active scopes; if zero
   remain the document is flag-archived (D.6) and a
   ``document.archived_orphan`` audit event is emitted with the payload
   documented in ``docs/roadmap/2026-05-04-hitl-and-extensions.md`` §4.6.
3. The KG subgraph for the archived document is cleaned up via the
   provided ``kg_reconciler`` callable. The KG cleanup is best-effort:
   per ADR-012's fire-and-log discipline, a Neo4j hiccup must not roll
   back the catalog state — the structural archive flag is the source
   of truth, and the KG can be regenerated from the catalog any time.

Per the no-delete policy: source data (bytes, raw extractions, semantic
JSON, Markdown assets) is preserved across this cascade. The KG
subgraph is the one explicit exception (it's a derived index,
regenerable from the catalog). The Archive/Purge Admin tool (D.9 —
deferred ADR) is the only path to physical deletion or rehydration of
archived documents.

Detection mechanism (D.7) — i.e. how this service learns that a Swym
community was deleted (webhook vs lazy detection on next read) — is
deferred Dassault docs work and explicitly not wired by this PR. The
:meth:`ScopeCascadeService.on_swym_community_deleted` convenience
wrapper exists so the future detection layer can call into the cascade
without re-deriving the (kind, ref) shape.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.schemas import APISchemaModel as BaseModel

if TYPE_CHECKING:
    from app.services.catalog_store import CatalogStore


log = logging.getLogger(__name__)


# Type alias for the optional KG-cleanup hook. Accepts ``document_id``
# and is allowed to raise — the cascade catches the exception and logs
# it (fire-and-log per ADR-012). Kept as a plain callable rather than a
# Protocol so the in-memory fakes used by the unit tests don't need to
# subclass anything.
KgReconciler = Callable[[str], None]


class CascadeFailure(BaseModel):
    """One per-document failure surfaced by :meth:`cascade_after_scope_removal`.

    Today only the archive transition itself can fail with this shape —
    KG cleanup failures are absorbed (fire-and-log) because the catalog
    is the source of truth and the KG is regenerable. The model exists
    so future failure modes (e.g. a database-locked write) have a place
    to live without bumping the result-shape contract.
    """

    document_id: str
    error: str


class CascadeResult(BaseModel):
    """Outcome of a single :meth:`cascade_after_scope_removal` invocation.

    - ``scope_links_flagged`` — how many ``document_scopes`` rows were
      transitioned from active to soft-removed by this call. Zero on a
      second cascade against the same ``(scope_kind, scope_ref)``
      because every link is already flagged (idempotent).
    - ``documents_archived`` — ids of documents that crossed the
      "no remaining active scopes" threshold during this call and were
      transitioned to archived. Documents that were already archived
      before the call do NOT appear here — the cascade is a strict
      transition counter, not a "currently archived" snapshot.
    - ``failures`` — per-document errors during the archive transition.
      KG cleanup failures are NOT recorded here; they're logged via the
      structured event ``knowledge.archive_cascade_kg_cleanup_failed``
      and the cascade continues.
    """

    scope_links_flagged: int
    documents_archived: list[str]
    failures: list[CascadeFailure]


class ScopeCascadeService:
    """Orchestrate the flag-only cascade when scope links are removed.

    Wire one of these per process. The catalog is the persistent state;
    the audit store receives the per-archive event. ``kg_reconciler``
    is optional — pass ``None`` (the default) when the knowledge layer
    is disabled or in test setups that don't care about KG state, and
    the cascade skips the KG cleanup step entirely.
    """

    def __init__(
        self,
        *,
        catalog: CatalogStore,
        kg_reconciler: KgReconciler | None = None,
    ) -> None:
        self._catalog = catalog
        self._kg_reconciler = kg_reconciler

    def cascade_after_scope_removal(
        self,
        scope_kind: str,
        scope_ref: str,
        *,
        actor: str,
    ) -> CascadeResult:
        """Soft-remove every link to ``(scope_kind, scope_ref)`` and archive orphans.

        Implementation walks the affected document set up-front (before
        flagging the scope links) so a concurrent
        :meth:`CatalogStore.add_scope` for the same ``(kind, ref)``
        doesn't fool the per-document orphan check into archiving a
        document that legitimately got re-linked while the cascade was
        running. The re-fetch step uses
        :meth:`CatalogStore.list_scopes_for_document` which already
        hides soft-removed links, so the orphan predicate is "no active
        scope rows remain" — the canonical D.6 condition.

        Returns a :class:`CascadeResult` so callers can introspect the
        outcome without re-querying. The detection-layer entry point
        (D.7 — Swym webhook / lazy detection) is expected to log the
        result and continue; the per-archive ``document.archived_orphan``
        audit event is the durable record.
        """
        # Gather candidate document ids by walking the (kind, ref) pair
        # to its active document set. We can't use
        # ``list_documents_in_scope`` here because that filters archived
        # docs, and we want to surface the documents that CURRENTLY
        # carry an active link to this scope — including documents
        # whose only remaining link is this scope (the orphan case
        # we're about to create).
        candidate_ids = list(self._candidate_document_ids(scope_kind, scope_ref))

        # Every candidate currently has an active link by construction
        # (``_candidate_document_ids`` only walks active rows). The
        # ``remove_scope`` call is idempotent, but we drive the
        # ``scope_links_flagged`` counter off the candidate set directly
        # so the result reflects the link transitions this cascade run
        # is responsible for — a second cascade against the same
        # ``(kind, ref)`` returns an empty candidate set and reports
        # zero links flagged.
        for document_id in candidate_ids:
            self._catalog.remove_scope(document_id, scope_kind, scope_ref)
        scope_links_flagged = len(candidate_ids)

        archived: list[str] = []
        failures: list[CascadeFailure] = []
        archived_at = datetime.now(UTC)
        for document_id in candidate_ids:
            remaining = self._catalog.list_scopes_for_document(document_id)
            if remaining:
                # Document still has at least one other active scope —
                # leave it alone (D.6 only fires when ALL links are
                # gone). This is the multi-scope happy path: a document
                # uploaded into both ``swym_community:abc`` and
                # ``personal:dev`` survives ``abc`` being deleted.
                continue
            # The document is now an orphan. Flag-archive it; the catalog
            # method is idempotent so a re-cascade against the same
            # community produces no audit-event flapping (the second
            # pass observes the same archived_at and we skip the audit
            # emit by checking whether the transition was fresh).
            try:
                document = self._catalog.flag_document_archived(
                    document_id,
                    archived_at=archived_at,
                    actor=actor,
                )
            except Exception as exc:  # noqa: BLE001 - per-doc failure isolation
                failures.append(CascadeFailure(document_id=document_id, error=str(exc)))
                continue

            # ``flag_document_archived`` is idempotent — it preserves
            # the original ``archived_at`` for an already-archived row.
            # Treat "the row's timestamp matches the one we just passed"
            # as a fresh transition; a mismatch means the row was
            # archived earlier and we suppress duplicate audit emits.
            is_fresh_archive = document.archived_at == archived_at
            if not is_fresh_archive:
                continue

            archived.append(document_id)
            self._cleanup_kg(document_id)
            self._emit_archived_orphan(
                document_id=document_id,
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                actor=actor,
            )

        return CascadeResult(
            scope_links_flagged=scope_links_flagged,
            documents_archived=archived,
            failures=failures,
        )

    def on_swym_community_deleted(
        self,
        community_id: str,
        *,
        actor: str,
    ) -> CascadeResult:
        """D.7 convenience wrapper: cascade for a Swym community deletion.

        Equivalent to ``cascade_after_scope_removal('swym_community',
        community_id, actor=...)``. The detection mechanism (webhook vs
        lazy on next access) is deferred Dassault docs work and is not
        wired by this PR — this entry point exists so the future
        detection layer can call into the cascade without re-deriving
        the ``(kind, ref)`` shape.
        """
        return self.cascade_after_scope_removal(
            "swym_community",
            community_id,
            actor=actor,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _candidate_document_ids(self, scope_kind: str, scope_ref: str) -> list[str]:
        """Return document ids currently linked (actively) to ``(kind, ref)``.

        Both store impls maintain a fast reverse lookup but neither
        exposes "give me the active link set even for archived docs"
        on the public Protocol. To keep the cascade store-agnostic
        without bloating the Protocol, we branch on the concrete impl
        via two well-known internal helpers:

        - :class:`InMemoryCatalogStore` keeps ``documents_by_scope`` as a
          plain dict reverse index.
        - :class:`SQLiteCatalogStore` exposes a ``_connect()`` context
          manager that hands us a connection to the join table.

        Anything else raises :class:`TypeError` — by construction the
        production wiring uses one of the two stores above and a
        future store would need the same internal hook to participate
        in the cascade.
        """
        ids: set[str] = set()

        # In-memory: a reverse index already exists.
        documents_by_scope = getattr(self._catalog, "documents_by_scope", None)
        if isinstance(documents_by_scope, dict):
            ids.update(documents_by_scope.get((scope_kind, scope_ref), set()))
            # Also walk the forward-index rows so we catch documents
            # that were already archived but still carry an active link
            # to this scope (defensive: such a state shouldn't exist,
            # but if it does the cascade should still re-flag the link).
            scopes_by_document = getattr(self._catalog, "scopes_by_document", {})
            if isinstance(scopes_by_document, dict):
                for document_id, scopes in scopes_by_document.items():
                    for scope in scopes:
                        if (
                            scope.kind == scope_kind
                            and scope.ref == scope_ref
                            and scope.removed_at is None
                        ):
                            ids.add(document_id)
                            break
            return sorted(ids)

        # SQLite: query the join table directly through the store's
        # ``_connect`` helper. This stays internal to the cascade
        # service so the public Protocol doesn't need a new method.
        connect = self._catalog._connect  # type: ignore[attr-defined]
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT document_id
                FROM document_scopes
                WHERE scope_kind = ? AND scope_ref = ? AND removed_at IS NULL
                """,
                (scope_kind, scope_ref),
            ).fetchall()
        ids.update(row[0] for row in rows)
        return sorted(ids)

    def _cleanup_kg(self, document_id: str) -> None:
        """Best-effort KG subgraph cleanup. Fire-and-log per ADR-012.

        KG state is a derived index regenerable from the catalog, so a
        Neo4j hiccup here must NOT roll back the structural archive
        flag. We log a structured warning so the operator can re-run
        knowledge reconciliation later if needed.
        """
        if self._kg_reconciler is None:
            return
        try:
            self._kg_reconciler(document_id)
        except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
            log.warning(
                "knowledge.archive_cascade_kg_cleanup_failed",
                extra={
                    "document_id": document_id,
                    "error": str(exc),
                },
            )

    def _emit_archived_orphan(
        self,
        *,
        document_id: str,
        scope_kind: str,
        scope_ref: str,
        actor: str,
    ) -> None:
        """Emit the ``document.archived_orphan`` structured event.

        Routes through :mod:`logging` so the audit handler installed at
        startup (#206) persists the row to ``audit_events``. The payload
        shape is documented in
        ``docs/roadmap/2026-05-04-hitl-and-extensions.md`` §4.6 and on
        the parent EPIC-D issue.
        """
        log.info(
            "document.archived_orphan",
            extra={
                "document_id": document_id,
                "scope_kind": scope_kind,
                "scope_ref": scope_ref,
                "actor": actor,
                "reason": "all_scopes_removed",
            },
        )


__all__ = [
    "CascadeFailure",
    "CascadeResult",
    "KgReconciler",
    "ScopeCascadeService",
]
