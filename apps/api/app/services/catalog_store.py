import base64
import binascii
import copy
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.models.document import (
    ALLOWED_PREDECESSORS,
    DocumentVersionStatus,
    IllegalTransition,
)
from app.schemas.document import Document, DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.scope import Scope
from app.schemas.semantic_document import SemanticDocument
from app.services.migrations import _run_migrations
from app.services.semantic_schema_loader import load_semantic_document

ReviewedStatus = DocumentVersionStatus  # narrowed to VALIDATED | REJECTED at the call site


class InvalidCursor(ValueError):
    """Raised when a pagination cursor cannot be decoded.

    The route layer maps this to HTTP 400 with the message in ``detail`` so
    clients can debug malformed cursors instead of seeing a 500.
    """


def _encode_cursor(position: tuple[datetime, str]) -> str:
    """Encode a ``(created_at, id)`` pair as an opaque URL-safe base64 token.

    The wire format is JSON inside base64 so the codec stays readable in
    server logs while remaining opaque to clients. Callers MUST treat the
    returned string as opaque — its shape is not part of the public API.
    """
    created_at, document_id = position
    payload = json.dumps([created_at.isoformat(), document_id]).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(token: str) -> tuple[datetime, str]:
    """Decode an opaque cursor back into a ``(created_at, id)`` tuple.

    Raises :class:`InvalidCursor` for malformed base64, malformed JSON,
    wrong shape (not a 2-element list), or wrong types. The error message
    is safe to surface to clients — it never leaks server state.
    """
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise InvalidCursor(f"Cursor is not valid base64: {exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursor(f"Cursor payload is not valid JSON: {exc}") from exc
    if not isinstance(decoded, list) or len(decoded) != 2:
        raise InvalidCursor("Cursor payload must be a [created_at, id] pair.")
    created_at_raw, document_id = decoded
    if not isinstance(created_at_raw, str) or not isinstance(document_id, str):
        raise InvalidCursor("Cursor fields must be strings.")
    try:
        created_at = datetime.fromisoformat(created_at_raw)
    except ValueError as exc:
        raise InvalidCursor(f"Cursor created_at is not an ISO datetime: {exc}") from exc
    return created_at, document_id


# How long a write may wait on a contended SQLite database before raising
# `database is locked`. 5 s is well above any healthy contention window in the
# MVP and short enough that a real deadlock surfaces before a request gateway
# times out.
_SQLITE_BUSY_TIMEOUT_MS = 5000


def _latest_status(
    document: Document,
    versions: dict[str, DocumentVersion],
) -> DocumentVersionStatus | None:
    """Resolve the latest version's status for in-memory filter routes.

    Used by :class:`InMemoryCatalogStore.list_documents` when ``status_filter``
    is set. Returns ``None`` when the document family has no versions or
    its ``latest_version_id`` is dangling (a state that shouldn't occur
    in practice but is handled defensively so the filter just skips
    such rows rather than raising).
    """
    version = versions.get(document.latest_version_id)
    if version is None and document.versions:
        version = document.versions[-1]
    return version.status if version is not None else None


class CatalogStore(Protocol):
    """Persistence boundary for document catalog metadata."""

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        """Return the first version with the hash, if one exists."""

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        """Persist a newly created document family and first version."""

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        """Append a new version to an existing document family and update
        ``Document.latest_version_id``."""

    def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        status_filter: frozenset[DocumentVersionStatus] | None = None,
        filename_query: str | None = None,
    ) -> list[Document]:
        """Return document families with their versions.

        Documents are returned sorted by ``(created_at ASC, id ASC)`` — the
        ``id`` tie-breaker keeps two same-second uploads from shifting
        between pages. When ``cursor`` is provided, only rows strictly
        greater than the encoded ``(created_at, id)`` tuple are returned.
        When ``limit`` is provided, at most ``limit`` rows are returned.

        ``status_filter`` (#86): when provided, only documents whose
        **latest version's status** is in the given set are returned.
        ``filename_query``: case-insensitive substring match against
        the document's ``original_filename``. Filters apply *before*
        pagination — the cursor semantics are "next page within the
        current filter set". A page short of ``limit`` still signals
        end-of-stream via the route layer.

        Raises :class:`InvalidCursor` if ``cursor`` cannot be decoded.
        """

    def get_document(self, document_id: str) -> Document | None:
        """Return one document family with versions."""

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        """Return one version within a document family."""

    def update_version_status(
        self,
        document_id: str,
        version_id: str,
        status: DocumentVersionStatus,
    ) -> DocumentVersion:
        """Persist a lifecycle status change and return the updated version.

        The write is guarded by the predecessor set derived from the FSM:
        the row is only updated if its current status is one of the states
        ``status`` is reachable from. If the row's status no longer matches
        (because another writer raced ahead) the implementation raises
        :class:`IllegalTransition` and leaves the catalog untouched.
        """

    def update_version_failure(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        """Mark a version FAILED and persist a human-readable failure reason."""

    def update_version_review(
        self,
        document_id: str,
        version_id: str,
        status: ReviewedStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> DocumentVersion:
        """Atomically write a reviewer's decision: status (VALIDATED or
        REJECTED), the optional note, and the timestamp it was made."""

    # ------- Generated artefacts (raw extraction + semantic output) ------- #

    def save_raw_extraction(self, version_id: str, raw_extraction: RawExtraction) -> None:
        """Persist parser output for a version. Replaces any prior extraction."""

    def get_raw_extraction(self, version_id: str) -> RawExtraction:
        """Return the persisted raw extraction. Raises KeyError if none exists."""

    def save_semantic_document(self, version_id: str, semantic: SemanticDocument) -> None:
        """Persist semantic JSON (and rendered Markdown if any) for a version."""

    def get_semantic_document(self, version_id: str) -> SemanticDocument:
        """Return the persisted semantic document. Raises KeyError if none exists.

        Implementations route through ``semantic_schema_loader`` so older
        persisted payloads are migrated to the current shape before being
        returned. Per ADR-008.
        """

    def get_semantic_document_payload(self, version_id: str) -> dict:
        """Return the raw persisted JSON payload as a dict.

        This is the read-side counterpart to ``save_semantic_document``: it
        returns the bytes-on-disk shape (whatever ``schema_version`` they
        carry) without coercing them through the current Pydantic model, so
        callers can route them through the schema loader explicitly. Raises
        KeyError if none exists.
        """

    # ------- Workspace scope membership (ADR-020 §1, EPIC-D D.1) ------- #

    def add_scope(self, document_id: str, scope: Scope) -> None:
        """Persist a ``(document_id, scope_kind, scope_ref)`` link.

        Two cases:

        - The triple does not exist → insert with the caller's
          ``added_at`` / ``added_by``. ``removed_at`` is NULL.
        - The triple exists but was soft-removed (``removed_at IS NOT
          NULL``) → reactivate by clearing ``removed_at`` and
          overwriting ``added_at`` / ``added_by`` with the caller's
          identity (a re-link is a fresh audit event).
        - The triple exists and is active (``removed_at IS NULL``) →
          no-op. The original first-write ``added_at`` / ``added_by``
          are preserved.

        ``scope.removed_at`` on input is ignored — this method is the
        public API for **adding/reactivating** a link. Use
        :meth:`remove_scope` to flag a link as removed.
        """

    def list_scopes_for_document(self, document_id: str) -> list[Scope]:
        """Return every active :class:`Scope` row for ``document_id``.

        Filters out soft-removed rows (``removed_at IS NOT NULL``) per
        the no-delete policy: removed scopes stay in the catalog for a
        future Archive/Purge Admin tool but they are invisible to
        normal reads. Order is insertion order. Returns an empty list
        when the document has no active scope links — including when
        ``document_id`` does not exist (the scope table is decoupled
        from the document family for forward-compat with bulk-link
        routes).
        """

    def list_documents_in_scope(
        self,
        scope_kind: str,
        scope_ref: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[Document], str | None]:
        """Return one page of documents that live in the given scope.

        Documents are returned sorted by ``(created_at ASC, id ASC)``,
        the same ordering ``list_documents`` uses, so the cursor
        codec is shared via :func:`_encode_cursor` /
        :func:`_decode_cursor`. The second tuple element is the
        cursor token to feed back as ``cursor`` for the next page,
        or ``None`` when this page is the last one.

        Soft-removed scope links (``removed_at IS NOT NULL``) are
        filtered out; only documents whose scope link is currently
        active are visible.

        Implementations look the (kind, ref) up via the
        ``idx_document_scopes_lookup`` index (SQLite) or the in-memory
        reverse index (in-memory store) and then materialise the
        ``Document`` objects so callers don't need a second round-trip.

        Raises :class:`InvalidCursor` if ``cursor`` cannot be decoded.
        """

    def remove_scope(self, document_id: str, scope_kind: str, scope_ref: str) -> None:
        """Soft-remove a single ``(document_id, scope_kind, scope_ref)`` link.

        Per the no-delete policy: the row stays in the catalog with
        ``removed_at`` set to the current UTC timestamp. Subsequent
        ``list_scopes_for_document`` / ``list_documents_in_scope``
        calls hide the link; :meth:`add_scope` for the same triple
        will reactivate it.

        Idempotent: flagging an already-removed or non-existent link
        is a no-op (``removed_at`` is not bumped on the second call —
        the original removal timestamp is preserved for audit). A
        future Archive/Purge Admin tool is the only path to physical
        deletion.
        """

    # ------- Archive flag (ADR-020 §4, EPIC-D D.6/D.7) ------- #

    def flag_document_archived(
        self,
        document_id: str,
        *,
        archived_at: datetime,
        actor: str,
    ) -> Document:
        """Soft-archive: set ``documents.archived_at = archived_at``.

        Idempotent: re-archiving an already-archived document preserves
        the original ``archived_at`` (audit-faithful). Returns the
        updated :class:`Document`. Raises :class:`KeyError` when the
        document is missing.

        Per the no-delete policy: this is a metadata transition only —
        no bytes, extractions, semantic JSON, or markdown assets are
        touched. The KG subgraph MAY be cleaned up by the caller as
        derived data (it's regenerable from the catalog and is the one
        explicit exception to the no-delete rule, per ADR-012 + the
        feedback rule).

        ``actor`` is the user id that triggered the cascade — it is
        not persisted on the document row (the audit row carries it)
        but is part of the contract so callers and instrumentation
        agree on the shape.
        """


class InMemoryCatalogStore:
    """In-memory catalog implementation for unit tests and fast local demos."""

    def __init__(self):
        self.documents: dict[str, Document] = {}
        self.versions_by_hash: dict[str, DocumentVersion] = {}
        self.versions: dict[str, DocumentVersion] = {}
        self.raw_extractions: dict[str, RawExtraction] = {}
        # Stored as raw JSON-shaped dicts (not typed SemanticDocument) so the
        # in-memory store mirrors the SQLite payload column and the loader
        # is the single boundary that yields a typed model. Per ADR-008.
        self.semantic_documents: dict[str, dict] = {}
        # ADR-020 §1, EPIC-D D.1. Forward index keyed by document_id so
        # ``list_scopes_for_document`` is O(1). The list ordering is
        # preserved-on-insert so callers can show "first linked" first.
        self.scopes_by_document: dict[str, list[Scope]] = {}
        # Reverse index keyed by (scope_kind, scope_ref) so
        # ``list_documents_in_scope`` is O(1) on the lookup and only
        # the page slice does any work. The set holds document_ids.
        self.documents_by_scope: dict[tuple[str, str], set[str]] = {}

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        return self.versions_by_hash.get(sha256)

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        self.documents[document.id] = document
        self.versions[version.id] = version
        if version.duplicate_of_version_id is None:
            self.versions_by_hash[version.sha256] = version

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError("Document not found.")
        document.versions.append(version)
        document.latest_version_id = version.id
        self.versions[version.id] = version
        if version.duplicate_of_version_id is None:
            self.versions_by_hash.setdefault(version.sha256, version)

    def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        status_filter: frozenset[DocumentVersionStatus] | None = None,
        filename_query: str | None = None,
    ) -> list[Document]:
        # Hide archived documents by default (ADR-020 §4 / D.6+D.7).
        # An admin-only ``include_archived=True`` query param could
        # surface them in a future Archive/Purge Admin tool; that's
        # explicitly out of scope for this slice — we route the
        # admin-tool path through ``_get_document_including_archived``
        # which is internal-only and never exposed via Protocol.
        ordered = sorted(
            (d for d in self.documents.values() if d.archived_at is None),
            key=lambda d: (d.created_at, d.id),
        )
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            ordered = [d for d in ordered if (d.created_at, d.id) > (after_created_at, after_id)]
        if status_filter is not None:
            ordered = [d for d in ordered if _latest_status(d, self.versions) in status_filter]
        if filename_query:
            needle = filename_query.lower()
            ordered = [d for d in ordered if needle in d.original_filename.lower()]
        if limit is not None:
            ordered = ordered[:limit]
        # #258 — populate ``Document.scopes`` per row. The forward
        # index is in-process so this is O(1) per doc; soft-removed
        # rows are filtered by ``list_scopes_for_document``.
        for document in ordered:
            document.scopes = self.list_scopes_for_document(document.id)
        return ordered

    def get_document(self, document_id: str) -> Document | None:
        # Hide archived documents from the standard read path (#265 / D.6);
        # the route layer maps ``None`` to a 404 — same shape as a missing
        # row, the correct hidden-existence story per ADR-020 §4. The
        # internal Archive/Purge Admin tool path uses
        # ``_get_document_including_archived``.
        document = self.documents.get(document_id)
        if document is None or document.archived_at is not None:
            return None
        # #258 — populate ``Document.scopes`` on detail reads too. Soft-
        # removed rows are filtered by ``list_scopes_for_document``.
        document.scopes = self.list_scopes_for_document(document.id)
        return document

    def _get_document_including_archived(self, document_id: str) -> Document | None:
        """Internal accessor that ignores ``archived_at``.

        Reserved for the future Archive/Purge Admin tool (D.9 — deferred
        ADR). Not exposed via the public :class:`CatalogStore` Protocol
        and never wired into a route in this PR. Kept here so the cascade
        service can confirm a document still exists (and capture its
        existing ``archived_at``) when re-archiving an already-archived
        row, without breaking the "reads hide archived" invariant.
        """
        return self.documents.get(document_id)

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError("Document not found.")
        for version in document.versions:
            if version.id == version_id:
                return version
        raise KeyError("Document version not found.")

    def update_version_status(
        self,
        document_id: str,
        version_id: str,
        status: DocumentVersionStatus,
    ) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        predecessors = ALLOWED_PREDECESSORS[status]
        if version.status not in predecessors:
            raise IllegalTransition(
                f"Cannot transition to {status.value}: expected current status in "
                f"{{{', '.join(sorted(s.value for s in predecessors))}}} "
                f"but found {version.status.value}."
            )
        version.status = status
        # Any transition to a non-FAILED status clears the version's
        # ``failure_reason``. Critical for the FAILED → EXTRACTING retry
        # path (#87): without this, a successfully-retried version
        # carries stale failure text forever. Idempotent for non-FAILED
        # rows since their ``failure_reason`` is already None.
        if status is not DocumentVersionStatus.FAILED:
            version.failure_reason = None
        return version

    def update_version_failure(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        version.status = DocumentVersionStatus.FAILED
        version.failure_reason = reason
        return version

    def update_version_review(
        self,
        document_id: str,
        version_id: str,
        status: ReviewedStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        version.status = status
        version.reviewer_note = reviewer_note
        version.reviewed_at = reviewed_at
        return version

    def save_raw_extraction(self, version_id: str, raw_extraction: RawExtraction) -> None:
        self.raw_extractions[version_id] = raw_extraction

    def get_raw_extraction(self, version_id: str) -> RawExtraction:
        raw_extraction = self.raw_extractions.get(version_id)
        if raw_extraction is None:
            raise KeyError("Raw extraction not found.")
        return raw_extraction

    def save_semantic_document(self, version_id: str, semantic: SemanticDocument) -> None:
        self.semantic_documents[version_id] = semantic.model_dump(mode="json")

    def get_semantic_document(self, version_id: str) -> SemanticDocument:
        return load_semantic_document(self.get_semantic_document_payload(version_id))

    def get_semantic_document_payload(self, version_id: str) -> dict:
        payload = self.semantic_documents.get(version_id)
        if payload is None:
            raise KeyError("Semantic output not found.")
        # Deep copy so callers can't mutate persisted state.
        return copy.deepcopy(payload)

    # ------- Workspace scope membership (ADR-020 §1, EPIC-D D.1) ------- #

    def add_scope(self, document_id: str, scope: Scope) -> None:
        existing = self.scopes_by_document.setdefault(document_id, [])
        for index, already in enumerate(existing):
            if already.kind == scope.kind and already.ref == scope.ref:
                if already.removed_at is not None:
                    # Reactivate: clear removed_at, overwrite added_at /
                    # added_by with the new caller's identity (re-link is
                    # a fresh audit event).
                    existing[index] = Scope(
                        kind=scope.kind,
                        ref=scope.ref,
                        added_at=scope.added_at,
                        added_by=scope.added_by,
                        removed_at=None,
                    )
                    self.documents_by_scope.setdefault((scope.kind, scope.ref), set()).add(
                        document_id
                    )
                # Active row exists → no-op (first-write wins for active
                # links; the original added_at / added_by are preserved).
                return
        # Fresh insert — store with removed_at coerced to None even if the
        # caller passed a non-None value (add_scope is the add path).
        existing.append(
            Scope(
                kind=scope.kind,
                ref=scope.ref,
                added_at=scope.added_at,
                added_by=scope.added_by,
                removed_at=None,
            )
        )
        self.documents_by_scope.setdefault((scope.kind, scope.ref), set()).add(document_id)

    def list_scopes_for_document(self, document_id: str) -> list[Scope]:
        # Filter soft-removed rows. Return a shallow copy so callers
        # can't mutate the persisted list.
        return [
            scope
            for scope in self.scopes_by_document.get(document_id, ())
            if scope.removed_at is None
        ]

    def list_documents_in_scope(
        self,
        scope_kind: str,
        scope_ref: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[Document], str | None]:
        # The reverse index is maintained to only contain active links;
        # remove_scope drops the doc id from the set when the link is
        # flagged. Archived rows are filtered out so a doc that lost its
        # last scope and was flag-archived disappears from the listing
        # even if a stale scope-link race leaves it in this index — the
        # archive flag is the source of truth for visibility.
        document_ids = self.documents_by_scope.get((scope_kind, scope_ref), set())
        candidates = [
            self.documents[d]
            for d in document_ids
            if d in self.documents and self.documents[d].archived_at is None
        ]
        ordered = sorted(candidates, key=lambda d: (d.created_at, d.id))
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            ordered = [d for d in ordered if (d.created_at, d.id) > (after_created_at, after_id)]
        page = ordered[:limit]
        # Emit a next cursor only when there's strictly more data behind
        # this page; mirrors the "page short of limit signals end" rule
        # used by ``list_documents``.
        if len(ordered) > limit and page:
            last = page[-1]
            next_cursor = _encode_cursor((last.created_at, last.id))
        else:
            next_cursor = None
        # #258 — populate ``Document.scopes`` for the returned page so
        # the catalog projection sees the same shape as ``GET
        # /documents``. Soft-removed rows are already filtered by
        # ``list_scopes_for_document``.
        for document in page:
            document.scopes = self.list_scopes_for_document(document.id)
        return page, next_cursor

    def remove_scope(self, document_id: str, scope_kind: str, scope_ref: str) -> None:
        # Soft-remove: flag the row with removed_at, drop from the
        # reverse index. The forward-index list keeps the row so
        # add_scope can reactivate it.
        existing = self.scopes_by_document.get(document_id)
        if existing is not None:
            for index, scope in enumerate(existing):
                if scope.kind == scope_kind and scope.ref == scope_ref and scope.removed_at is None:
                    existing[index] = Scope(
                        kind=scope.kind,
                        ref=scope.ref,
                        added_at=scope.added_at,
                        added_by=scope.added_by,
                        removed_at=datetime.now(UTC),
                    )
                    break
        reverse = self.documents_by_scope.get((scope_kind, scope_ref))
        if reverse is not None:
            reverse.discard(document_id)
            if not reverse:
                del self.documents_by_scope[(scope_kind, scope_ref)]

    def flag_document_archived(
        self,
        document_id: str,
        *,
        archived_at: datetime,
        actor: str,  # noqa: ARG002 — kept for Protocol parity; audit row carries actor.
    ) -> Document:
        # Use the internal accessor so an already-archived row still
        # resolves and we can preserve its original ``archived_at``.
        document = self._get_document_including_archived(document_id)
        if document is None:
            raise KeyError("Document not found.")
        if document.archived_at is None:
            # First-archive: stamp the timestamp on the existing row so
            # references held elsewhere (e.g. the test fixture's local
            # variable) observe the transition.
            document.archived_at = archived_at
        # Second-archive: no-op — the original ``archived_at`` is
        # preserved (audit-faithful). Return the row either way.
        return document


class SQLiteCatalogStore:
    """SQLite-backed catalog store for the local persistent MVP."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Use isolation_level=None (manual / autocommit mode) for migrations
        # so Python's sqlite3 module does not issue implicit COMMITs before
        # DDL statements, which would break SAVEPOINT-based rollback.
        conn = sqlite3.connect(self.database_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        try:
            # WAL is per-database and survives restart, so set once here.
            # WAL allows a writer + readers concurrently and reduces
            # `database is locked` errors during the eventual extraction
            # worker / API request overlap.
            conn.execute("PRAGMA journal_mode = WAL")
            _run_migrations(conn)
        finally:
            conn.close()

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        # Excluding rows where `duplicate_of_version_id IS NOT NULL` matches
        # the in-memory store's behaviour (it never indexes duplicates by
        # hash) and prevents a third upload of the same bytes from chaining
        # off a duplicate row instead of pointing at the original version.
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE sha256 = ? AND duplicate_of_version_id IS NULL
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (sha256,),
            ).fetchone()
        return self._version_from_row(row) if row else None

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (id, original_filename, latest_version_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    document.id,
                    document.original_filename,
                    document.latest_version_id,
                    document.created_at.isoformat(),
                ),
            )
            self._insert_version(connection, version)

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        # Use the archived-inclusive lookup so write paths continue to
        # work on archived rows when the future Archive/Purge Admin
        # tool wires up rehydration. The standard surface never appends
        # to an archived document because the route layer 404s before
        # reaching this service.
        if self._get_document_including_archived(document_id) is None:
            raise KeyError("Document not found.")
        with self._connect() as connection:
            self._insert_version(connection, version)
            connection.execute(
                "UPDATE documents SET latest_version_id = ? WHERE id = ?",
                (version.id, document_id),
            )

    def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        status_filter: frozenset[DocumentVersionStatus] | None = None,
        filename_query: str | None = None,
    ) -> list[Document]:
        # Build the documents query with optional cursor / status /
        # filename / limit clauses. The tuple comparison
        # `(d.created_at, d.id) > (?, ?)` is supported by SQLite directly
        # and matches the in-memory store's ordering. Status filter
        # joins on the latest version row; the LEFT JOIN keeps documents
        # without versions visible when the filter is absent.
        # ``d.archived_at IS NULL`` hides flag-archived rows by default
        # (ADR-020 §4) — a future Archive/Purge Admin tool can build
        # its own listing by going through ``_get_document_including_archived``.
        clauses: list[str] = ["d.archived_at IS NULL"]
        params: list[object] = []
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            clauses.append("(d.created_at, d.id) > (?, ?)")
            params.extend([after_created_at.isoformat(), after_id])
        if status_filter is not None:
            placeholders = ", ".join("?" for _ in status_filter)
            clauses.append(f"latest.status IN ({placeholders})")
            params.extend(s.value for s in status_filter)
        if filename_query:
            clauses.append("LOWER(d.original_filename) LIKE ?")
            params.append(f"%{filename_query.lower()}%")
        where_clause = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(int(limit))
        query = (
            "SELECT d.* FROM documents d "
            "LEFT JOIN document_versions latest "
            "  ON latest.id = d.latest_version_id"
            + where_clause
            + " ORDER BY d.created_at ASC, d.id ASC"
            + limit_clause
        )
        with self._connect() as connection:
            document_rows = connection.execute(query, tuple(params)).fetchall()
            if not document_rows:
                return []
            # Only fetch versions belonging to the slice we're returning so
            # we don't read the whole `document_versions` table per page.
            ids = [row["id"] for row in document_rows]
            placeholders = ", ".join("?" for _ in ids)
            version_rows = connection.execute(
                f"""
                SELECT * FROM document_versions
                WHERE document_id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                tuple(ids),
            ).fetchall()
            # #258 — batch-load active scope links for the page in a
            # single query so the read path stays N+1-free. The
            # ``removed_at IS NULL`` predicate matches the no-delete
            # policy (#262); flagged links are invisible.
            scopes_by_document = self._batch_list_scopes(connection, ids)
        versions_by_document: dict[str, list[DocumentVersion]] = {}
        for row in version_rows:
            version = self._version_from_row(row)
            versions_by_document.setdefault(version.document_id, []).append(version)
        return [
            self._document_from_row(
                row,
                versions_by_document.get(row["id"], []),
                scopes_by_document.get(row["id"], []),
            )
            for row in document_rows
        ]

    def get_document(self, document_id: str) -> Document | None:
        # Filter ``archived_at IS NULL`` so flag-archived docs return
        # None — the route layer maps that to a 404 (hidden-existence).
        # The future Archive/Purge Admin tool reaches archived rows via
        # ``_get_document_including_archived``.
        with self._connect() as connection:
            document_row = connection.execute(
                "SELECT * FROM documents WHERE id = ? AND archived_at IS NULL",
                (document_id,),
            ).fetchone()
            if document_row is None:
                return None
            version_rows = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE document_id = ?
                ORDER BY created_at ASC
                """,
                (document_id,),
            ).fetchall()
        versions = [self._version_from_row(row) for row in version_rows]
        # #258 — populate scopes on detail reads. Soft-removed rows are
        # filtered by ``list_scopes_for_document``.
        scopes = self.list_scopes_for_document(document_id)
        return self._document_from_row(document_row, versions, scopes)

    def _get_document_including_archived(self, document_id: str) -> Document | None:
        """Internal accessor that ignores ``archived_at``.

        Reserved for the future Archive/Purge Admin tool (D.9, deferred
        ADR). Not exposed via the public :class:`CatalogStore` Protocol
        and never wired into a route in this PR. Used by the cascade
        service to resolve an already-archived document during the
        idempotent re-archive path so the original timestamp can be
        preserved without breaking the "reads hide archived" invariant.
        """
        with self._connect() as connection:
            document_row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if document_row is None:
                return None
            version_rows = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE document_id = ?
                ORDER BY created_at ASC
                """,
                (document_id,),
            ).fetchall()
            # #258 — populate ``Document.scopes`` on detail reads too.
            # Goes through the same ``list_scopes_for_document`` path so
            # soft-removed links are filtered.
            scope_rows = connection.execute(
                """
                SELECT scope_kind, scope_ref, added_at, added_by, removed_at
                FROM document_scopes
                WHERE document_id = ? AND removed_at IS NULL
                ORDER BY added_at ASC
                """,
                (document_id,),
            ).fetchall()
        versions = [self._version_from_row(row) for row in version_rows]
        scopes = [self._scope_from_row(row) for row in scope_rows]
        return self._document_from_row(document_row, versions, scopes)

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE document_id = ? AND id = ?
                """,
                (document_id, version_id),
            ).fetchone()
        if row is None:
            # Use the archived-inclusive lookup so the disambiguation
            # between "document missing" and "version missing" still
            # holds for archived documents. ``get_version`` is a
            # write-path helper used by lifecycle transitions; archive
            # state is not its concern.
            document_exists = self._get_document_including_archived(document_id) is not None
            if not document_exists:
                raise KeyError("Document not found.")
            raise KeyError("Document version not found.")
        return self._version_from_row(row)

    def update_version_status(
        self,
        document_id: str,
        version_id: str,
        status: DocumentVersionStatus,
    ) -> DocumentVersion:
        # Confirm the row exists at all so a missing version still raises
        # KeyError ("Document not found." / "Document version not found.")
        # rather than masquerading as a concurrency conflict.
        self.get_version(document_id=document_id, version_id=version_id)
        predecessors = ALLOWED_PREDECESSORS[status]
        # Build an "?, ?, ..." placeholder list so the predecessor set is
        # bound as parameters (sqlite3 won't expand a list inside a single
        # placeholder). An empty predecessor set means the FSM has no edges
        # leading into ``status`` — the UPDATE will match zero rows and the
        # rowcount==0 branch below raises with a clear message.
        if predecessors:
            placeholders = ", ".join("?" for _ in predecessors)
            predecessor_values = tuple(s.value for s in predecessors)
        else:
            # SQLite rejects "IN ()". Use a sentinel that no real status
            # equals so the UPDATE matches zero rows by construction.
            placeholders = "?"
            predecessor_values = ("__no_legal_predecessor__",)
        # Any transition to a non-FAILED status clears ``failure_reason``
        # so a successfully-retried version (FAILED → EXTRACTING → ...)
        # doesn't carry stale failure text forever (#87). Idempotent for
        # non-FAILED rows since their ``failure_reason`` is already NULL.
        clear_failure = status is not DocumentVersionStatus.FAILED
        with self._connect() as connection:
            update_clause = (
                "SET status = ?, failure_reason = NULL" if clear_failure else "SET status = ?"
            )
            cursor = connection.execute(
                f"""
                UPDATE document_versions
                {update_clause}
                WHERE document_id = ? AND id = ? AND status IN ({placeholders})
                """,
                (status.value, document_id, version_id, *predecessor_values),
            )
            if cursor.rowcount == 0:
                actual_row = connection.execute(
                    """
                    SELECT status FROM document_versions
                    WHERE document_id = ? AND id = ?
                    """,
                    (document_id, version_id),
                ).fetchone()
                actual_status = actual_row["status"] if actual_row else "<missing>"
                expected = (
                    ", ".join(sorted(s.value for s in predecessors)) if predecessors else "<none>"
                )
                raise IllegalTransition(
                    f"Cannot transition to {status.value}: expected current status in "
                    f"{{{expected}}} but found {actual_status}."
                )
        return self.get_version(document_id=document_id, version_id=version_id)

    def update_version_failure(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        self.get_version(document_id=document_id, version_id=version_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE document_versions
                SET status = ?, failure_reason = ?
                WHERE document_id = ? AND id = ?
                """,
                (DocumentVersionStatus.FAILED.value, reason, document_id, version_id),
            )
        return self.get_version(document_id=document_id, version_id=version_id)

    def update_version_review(
        self,
        document_id: str,
        version_id: str,
        status: ReviewedStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> DocumentVersion:
        self.get_version(document_id=document_id, version_id=version_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE document_versions
                SET status = ?, reviewer_note = ?, reviewed_at = ?
                WHERE document_id = ? AND id = ?
                """,
                (status.value, reviewer_note, reviewed_at.isoformat(), document_id, version_id),
            )
        return self.get_version(document_id=document_id, version_id=version_id)

    def save_raw_extraction(self, version_id: str, raw_extraction: RawExtraction) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO raw_extractions (document_version_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(document_version_id) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    version_id,
                    raw_extraction.model_dump_json(),
                    raw_extraction.created_at.isoformat(),
                ),
            )

    def get_raw_extraction(self, version_id: str) -> RawExtraction:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM raw_extractions WHERE document_version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Raw extraction not found.")
        return RawExtraction.model_validate_json(row["payload"])

    def save_semantic_document(self, version_id: str, semantic: SemanticDocument) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO semantic_documents (document_version_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(document_version_id) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    version_id,
                    semantic.model_dump_json(),
                    semantic.created_at.isoformat(),
                ),
            )

    def get_semantic_document(self, version_id: str) -> SemanticDocument:
        return load_semantic_document(self.get_semantic_document_payload(version_id))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit on success, and **always close**.

        Python's `sqlite3.Connection` is itself a context manager that
        commits on exit but does NOT release the underlying handle —
        relying on it leaks one file descriptor per call. Wrapping in
        an explicit `@contextmanager` closes the connection, enables
        foreign-key enforcement, and sets a busy timeout for every
        operation.
        """
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _insert_version(self, connection: sqlite3.Connection, version: DocumentVersion) -> None:
        connection.execute(
            """
            INSERT INTO document_versions (
                id,
                document_id,
                version_number,
                filename,
                content_type,
                file_size,
                sha256,
                storage_uri,
                status,
                duplicate_of_version_id,
                failure_reason,
                reviewer_note,
                reviewed_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version.id,
                version.document_id,
                version.version_number,
                version.filename,
                version.content_type,
                version.file_size,
                version.sha256,
                version.storage_uri,
                version.status.value,
                version.duplicate_of_version_id,
                version.failure_reason,
                version.reviewer_note,
                version.reviewed_at.isoformat() if version.reviewed_at else None,
                version.created_at.isoformat(),
            ),
        )

    def _document_from_row(
        self,
        row: sqlite3.Row,
        versions: list[DocumentVersion],
        scopes: list[Scope] | None = None,
    ) -> Document:
        # Migration 0006 guarantees ``archived_at`` is on every row; it
        # may be NULL but the SELECT always includes it. Falling back to
        # ``None`` via ``row.keys()`` would also work, but the explicit
        # guard documents the column contract.
        archived_at = row["archived_at"] if "archived_at" in row.keys() else None  # noqa: SIM118 — sqlite3.Row supports `in` only via .keys()
        return Document(
            id=row["id"],
            original_filename=row["original_filename"],
            latest_version_id=row["latest_version_id"],
            created_at=row["created_at"],
            archived_at=archived_at,
            versions=versions,
            scopes=scopes if scopes is not None else [],
        )

    def _batch_list_scopes(
        self,
        connection: sqlite3.Connection,
        document_ids: list[str],
    ) -> dict[str, list[Scope]]:
        """Group active scope links by ``document_id`` in a single query.

        Issued once per page (``list_documents`` /
        ``list_documents_in_scope``) so the read path stays N+1-free
        when ``Document.scopes`` is populated (#258). Soft-removed
        links (``removed_at IS NOT NULL``) are filtered per the
        no-delete policy (#262); flagged rows stay in the table for a
        future Archive/Purge tool but are invisible to reads.

        Returns a dict keyed by ``document_id``; documents with no
        active scope link are absent from the dict (callers fall back
        to an empty list). Order within each list is ``added_at ASC``,
        matching :meth:`list_scopes_for_document`.
        """
        if not document_ids:
            return {}
        placeholders = ", ".join("?" for _ in document_ids)
        rows = connection.execute(
            f"""
            SELECT document_id, scope_kind, scope_ref, added_at, added_by, removed_at
            FROM document_scopes
            WHERE document_id IN ({placeholders}) AND removed_at IS NULL
            ORDER BY added_at ASC
            """,
            tuple(document_ids),
        ).fetchall()
        grouped: dict[str, list[Scope]] = {}
        for row in rows:
            grouped.setdefault(row["document_id"], []).append(self._scope_from_row(row))
        return grouped

    def _version_from_row(self, row: sqlite3.Row) -> DocumentVersion:
        return DocumentVersion(
            id=row["id"],
            document_id=row["document_id"],
            version_number=row["version_number"],
            filename=row["filename"],
            content_type=row["content_type"],
            file_size=row["file_size"],
            sha256=row["sha256"],
            storage_uri=row["storage_uri"],
            status=DocumentVersionStatus(row["status"]),
            duplicate_of_version_id=row["duplicate_of_version_id"],
            failure_reason=row["failure_reason"],
            reviewer_note=row["reviewer_note"],
            reviewed_at=row["reviewed_at"],
            created_at=row["created_at"],
        )

    def get_semantic_document_payload(self, version_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM semantic_documents WHERE document_version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Semantic output not found.")
        return json.loads(row["payload"])

    # ------- Workspace scope membership (ADR-020 §1, EPIC-D D.1) ------- #

    def add_scope(self, document_id: str, scope: Scope) -> None:
        # UPSERT pattern: inserting a fresh row stores added_at/added_by;
        # hitting the (document_id, scope_kind, scope_ref) PK on a
        # previously soft-removed row reactivates it (clear removed_at,
        # overwrite added_at/added_by with the re-link caller's identity).
        # Hitting the PK on an active row is a no-op via the WHERE clause
        # on the DO UPDATE — first-write wins for active links.
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO document_scopes (
                    document_id, scope_kind, scope_ref, added_at, added_by, removed_at
                )
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(document_id, scope_kind, scope_ref) DO UPDATE SET
                    removed_at = NULL,
                    added_at = excluded.added_at,
                    added_by = excluded.added_by
                WHERE document_scopes.removed_at IS NOT NULL
                """,
                (
                    document_id,
                    scope.kind,
                    scope.ref,
                    scope.added_at.isoformat(),
                    scope.added_by,
                ),
            )

    def list_scopes_for_document(self, document_id: str) -> list[Scope]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT scope_kind, scope_ref, added_at, added_by, removed_at
                FROM document_scopes
                WHERE document_id = ? AND removed_at IS NULL
                ORDER BY added_at ASC
                """,
                (document_id,),
            ).fetchall()
        return [self._scope_from_row(row) for row in rows]

    def list_documents_in_scope(
        self,
        scope_kind: str,
        scope_ref: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[Document], str | None]:
        # Fetch ``limit + 1`` rows so we can emit a next-cursor only when
        # there's strictly more data behind the page. Matches the
        # in-memory impl's "page short of limit signals end" rule.
        # ``s.removed_at IS NULL`` filters soft-removed scope links per
        # the no-delete policy — flagged rows stay in the table for the
        # future Archive/Purge Admin tool but are invisible to reads.
        # ``d.archived_at IS NULL`` additionally hides documents that
        # were flag-archived by the orphan cascade (ADR-020 §4).
        clauses: list[str] = [
            "s.scope_kind = ?",
            "s.scope_ref = ?",
            "s.removed_at IS NULL",
            "d.archived_at IS NULL",
        ]
        params: list[object] = [scope_kind, scope_ref]
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            clauses.append("(d.created_at, d.id) > (?, ?)")
            params.extend([after_created_at.isoformat(), after_id])
        params.append(int(limit) + 1)
        query = (
            "SELECT d.* FROM document_scopes s "
            "INNER JOIN documents d ON d.id = s.document_id "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY d.created_at ASC, d.id ASC "
            "LIMIT ?"
        )
        with self._connect() as connection:
            document_rows = connection.execute(query, tuple(params)).fetchall()
            if not document_rows:
                return [], None
            ids = [row["id"] for row in document_rows[:limit]]
            placeholders = ", ".join("?" for _ in ids)
            version_rows = connection.execute(
                f"""
                SELECT * FROM document_versions
                WHERE document_id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                tuple(ids),
            ).fetchall()
            # #258 — batch-load active scope links for the page so
            # the catalog projection sees the same shape as ``GET
            # /documents``. Stays N+1-free.
            scopes_by_document = self._batch_list_scopes(connection, ids)
        versions_by_document: dict[str, list[DocumentVersion]] = {}
        for row in version_rows:
            version = self._version_from_row(row)
            versions_by_document.setdefault(version.document_id, []).append(version)
        page_rows = document_rows[:limit]
        page = [
            self._document_from_row(
                row,
                versions_by_document.get(row["id"], []),
                scopes_by_document.get(row["id"], []),
            )
            for row in page_rows
        ]
        if len(document_rows) > limit and page:
            last = page[-1]
            next_cursor: str | None = _encode_cursor((last.created_at, last.id))
        else:
            next_cursor = None
        return page, next_cursor

    def remove_scope(self, document_id: str, scope_kind: str, scope_ref: str) -> None:
        # Soft-remove: stamp removed_at on the active row only. Already-
        # removed rows preserve their original removed_at timestamp;
        # non-existent rows produce zero rowcount (idempotent). The row
        # is never physically deleted — the future Archive/Purge Admin
        # tool is the only path to that.
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE document_scopes
                SET removed_at = ?
                WHERE document_id = ?
                  AND scope_kind = ?
                  AND scope_ref = ?
                  AND removed_at IS NULL
                """,
                (
                    datetime.now(UTC).isoformat(),
                    document_id,
                    scope_kind,
                    scope_ref,
                ),
            )

    def _scope_from_row(self, row: sqlite3.Row) -> Scope:
        # Migration 0005 guarantees ``removed_at`` is on every row; the
        # column may be NULL but the SELECT always includes it.
        return Scope(
            kind=row["scope_kind"],
            ref=row["scope_ref"],
            added_at=row["added_at"],
            added_by=row["added_by"],
            removed_at=row["removed_at"],
        )

    # ------- Archive flag (ADR-020 §4, EPIC-D D.6/D.7) ------- #

    def flag_document_archived(
        self,
        document_id: str,
        *,
        archived_at: datetime,
        actor: str,  # noqa: ARG002 — kept for Protocol parity; audit row carries actor.
    ) -> Document:
        # Idempotent flag: only set ``archived_at`` when it's still NULL
        # so a re-archive preserves the original timestamp (audit-faithful).
        # Confirm the row exists first so a missing document raises
        # KeyError instead of silently no-op'ing into a misleading return.
        document = self._get_document_including_archived(document_id)
        if document is None:
            raise KeyError("Document not found.")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET archived_at = ?
                WHERE id = ? AND archived_at IS NULL
                """,
                (archived_at.isoformat(), document_id),
            )
        # Re-read so the returned Document carries the current
        # ``archived_at`` (either the new timestamp or, for an idempotent
        # second-archive, the original one).
        refreshed = self._get_document_including_archived(document_id)
        assert refreshed is not None  # we just confirmed existence
        return refreshed
