"""Ordered schema migrations for the SQLite catalog database.

Contract
--------
Each entry in ``MIGRATIONS`` is a ``(migration_id, callable)`` pair where
``migration_id`` is a unique, lexicographically-ordered string (e.g.
``"0001_initial"``) and the callable accepts a single :class:`sqlite3.Connection`
and performs the DDL for that migration step.

``_run_migrations(conn)`` is the public entry point:

1. Ensures the ``schema_migrations`` tracking table exists.
2. Reads the set of already-applied IDs.
3. For each unapplied migration, runs the callable inside its own
   ``SAVEPOINT``/``RELEASE``/``ROLLBACK TO SAVEPOINT`` transaction so a
   failure rolls back only that migration and does not corrupt work done by
   earlier steps.
4. Inserts the migration ID into ``schema_migrations`` on success.

Backwards-compatibility bootstrap
----------------------------------
If ``schema_migrations`` is empty **and** any of the legacy tables already
exist (created by the old ``_initialize`` ad-hoc approach), all current
migration IDs are recorded as applied without re-running their callables.
This lets existing on-disk databases be adopted cleanly.

Adding a new migration
----------------------
1. Define a function ``def _migrate_NNNN_name(conn: sqlite3.Connection) -> None``.
2. Append ``("NNNN_name", _migrate_NNNN_name)`` to ``MIGRATIONS``.
   The list is the authoritative order — do not renumber existing entries.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Individual migration steps
# ---------------------------------------------------------------------------


def _migrate_0001_initial(conn: sqlite3.Connection) -> None:
    """Baseline schema: documents, document_versions (with review columns),
    sha256 index, raw_extractions, and semantic_documents tables."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            latest_version_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            status TEXT NOT NULL,
            duplicate_of_version_id TEXT,
            failure_reason TEXT,
            reviewer_note TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_versions_sha256
        ON document_versions (sha256)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_extractions (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic_documents (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
        )
        """
    )


def _migrate_0002_add_review_columns(conn: sqlite3.Connection) -> None:
    """Add ``reviewer_note`` and ``reviewed_at`` to pre-existing databases
    that were created before those columns were introduced.

    ``CREATE TABLE IF NOT EXISTS`` in migration 0001 already includes these
    columns for brand-new databases, so this migration is a no-op for fresh
    installs. For legacy databases that skipped 0001 via the bootstrap path,
    it adds the columns if they are absent (SQLite does not support
    ``ADD COLUMN IF NOT EXISTS``, so we inspect ``PRAGMA table_info`` first).

    Note: ``PRAGMA table_info`` returns rows as ``(cid, name, type,
    notnull, dflt_value, pk)`` — we use positional index 1 so this
    function does not depend on ``row_factory`` being set on the caller's
    connection.
    """
    # Index 1 is the column name in PRAGMA table_info output.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(document_versions)").fetchall()}
    if "reviewer_note" not in existing:
        conn.execute("ALTER TABLE document_versions ADD COLUMN reviewer_note TEXT")
    if "reviewed_at" not in existing:
        conn.execute("ALTER TABLE document_versions ADD COLUMN reviewed_at TEXT")


def _migrate_0003_perf_indexes(conn: sqlite3.Connection) -> None:
    """Add indexes on hot read paths uncovered by the 2026-05-04 audit (#224).

    1. ``idx_document_versions_document_id`` — the ``list_documents`` page
       fetches versions for the returned slice via
       ``WHERE document_id IN (...)``. Without this index that secondary
       query scans the full ``document_versions`` table on every page.
    2. ``idx_documents_created_at_id`` — cursor pagination orders by
       ``(d.created_at, d.id)`` and predicates the cursor as
       ``(d.created_at, d.id) > (?, ?)``. The composite covers both the
       sort and the cursor predicate so the planner can serve pages
       directly from the index.

    Both indexes use ``IF NOT EXISTS`` so re-running this migration on a
    database where the indexes were created out-of-band is safe.
    """
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_versions_document_id "
        "ON document_versions (document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_created_at_id ON documents (created_at, id)"
    )


def _migrate_0005_document_scopes_removed_at(conn: sqlite3.Connection) -> None:
    """Add ``removed_at`` to ``document_scopes`` for soft-remove semantics.

    Per the no-delete policy (no real deletion of document source data —
    flag-only, real purge handled by a future Archive/Purge Admin tool),
    ``CatalogStore.remove_scope`` no longer deletes the row but flags
    it with a ``removed_at`` timestamp. ``add_scope`` reactivates a
    flagged row instead of failing the PK constraint.

    SQLite does not support ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``,
    so we inspect ``PRAGMA table_info`` first.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(document_scopes)").fetchall()}
    if "removed_at" not in existing:
        conn.execute("ALTER TABLE document_scopes ADD COLUMN removed_at TEXT")


def _migrate_0006_documents_archived_at(conn: sqlite3.Connection) -> None:
    """Add ``archived_at`` to ``documents`` for the flag-only orphan cascade.

    Per the no-delete policy and ADR-020 §4 (rewritten in #262): when a
    document loses its last active scope link (e.g. its only Swym
    community got deleted), the cascade flags the document with an
    ``archived_at`` timestamp instead of physically deleting bytes,
    extractions, semantic JSON, or markdown assets. Read paths
    (``list_documents``, ``list_documents_in_scope``, ``get_document``,
    ``/knowledge/catalog``) filter ``archived_at IS NULL`` so archived
    rows are invisible to the standard surface but stay recoverable
    until the future Archive/Purge Admin tool acts on them.

    SQLite does not support ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``,
    so we inspect ``PRAGMA table_info`` first — same pattern as
    migration 0002 / 0005.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "archived_at" not in existing:
        conn.execute("ALTER TABLE documents ADD COLUMN archived_at TEXT")


def _migrate_0007_validation_metadata(conn: sqlite3.Connection) -> None:
    """HITL validation metadata sidecar (ADR-023, EPIC-A A.5, #215).

    Sidecar table keyed by ``version_id`` that holds the 5-signal
    confidence breakdown plus the routing decision the
    ``hitl_router.py`` next slice will write. Kept off the public
    ``Document`` / ``DocumentVersion`` API surface per EPIC-A's
    "auto-validated == human-validated to consumers" rule, so the
    visibility is by-construction (no route reads from this table on
    the public read path).

    The JSON-text columns (``confidence_signals`` / ``confidence_weights``)
    serialise the per-signal dicts; SQLite's JSON support is sufficient
    for the v1 ad-hoc audit queries we need ("show me every
    auto-validated version with orphan_ratio > 0.3"). A future
    migration can promote them to typed columns without breaking the
    contract — the sidecar isolation is the load-bearing property.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_metadata (
            version_id        TEXT PRIMARY KEY,
            confidence_overall REAL,
            confidence_signals TEXT,
            confidence_weights TEXT,
            ocr_override_active INTEGER,
            confidence_computed_at TEXT,
            confidence_computed_by_version TEXT,
            routing_decision TEXT,
            validation_method TEXT,
            validation_actor TEXT,
            FOREIGN KEY (version_id) REFERENCES document_versions(id)
        )
        """
    )


def _migrate_0008_corpus_norms(conn: sqlite3.Connection) -> None:
    """Corpus norms backing the length / asset z-score signals (ADR-023 §1, §4).

    Stores ``(mean, stddev, sample_count)`` per
    ``(content_type, topic_cluster, metric_name)`` bucket. The scorer
    consults this table to compute z-scores for the
    ``section_length`` and ``asset_count`` signals; missing buckets
    score ``1.0`` (cold-start tolerance, see ADR-023 §1).

    Materialised on-demand: the first request for an unknown bucket
    triggers a one-time scan of the catalog's existing semantic
    documents to compute the norms and persist the row. The compound
    primary key keeps a bucket from being recorded twice; an ``INSERT
    OR REPLACE`` on the recompute path is idempotent.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus_norms (
            content_type    TEXT NOT NULL,
            topic_cluster   TEXT NOT NULL,
            metric_name     TEXT NOT NULL,
            sample_count    INTEGER NOT NULL,
            mean            REAL NOT NULL,
            stddev          REAL NOT NULL,
            updated_at      TEXT NOT NULL,
            PRIMARY KEY (content_type, topic_cluster, metric_name)
        )
        """
    )


def _migrate_0009_sampling_state(conn: sqlite3.Connection) -> None:
    """SPC sampling state per ``(content_type, topic_cluster)`` bucket
    (ADR-023 §6, EPIC-A A.3, #215).

    Backs :class:`SQLiteSamplingStateStore`. The HITL router bumps
    these counters every time it makes a routing decision so the
    future drift detector can detect "this bucket's auto-rate is
    diverging from its observed human-flip rate" without a SQL
    materialisation pass over the audit table.

    Counters are non-negative and monotonic — the router only ever
    increments — and ``samples_human_after_auto`` is reserved for the
    drift signal the auto-promotion / drift-detector worker (next
    slice) writes when a human reviewer overturns a previously-auto
    decision.

    The compound primary key keeps a bucket from being recorded twice
    and lets the in-memory + SQLite implementations share an
    ``INSERT OR IGNORE`` + ``UPDATE`` write pattern that's portable
    across SQLite versions older than the UPSERT cutoff (3.24).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sampling_state (
            content_type            TEXT NOT NULL,
            topic_cluster           TEXT NOT NULL,
            samples_taken           INTEGER NOT NULL DEFAULT 0,
            samples_auto            INTEGER NOT NULL DEFAULT 0,
            samples_human           INTEGER NOT NULL DEFAULT 0,
            samples_human_after_auto INTEGER NOT NULL DEFAULT 0,
            last_decision_at        TEXT,
            PRIMARY KEY (content_type, topic_cluster)
        )
        """
    )


def _migrate_0004_document_scopes(conn: sqlite3.Connection) -> None:
    """Workspace scoping (ADR-020 §1, EPIC-D D.1, #218).

    Creates the ``document_scopes`` join table that links a document
    family to one or more scopes (``personal`` / ``swym_community`` /
    ``project``). A document can live in N scopes simultaneously; the
    primary key ``(document_id, scope_kind, scope_ref)`` keeps the
    same scope from being recorded twice for the same document.

    The single supporting index covers the read pattern "list documents
    in scope X" used by the future EPIC-D D.5 filter on every list /
    search / graph endpoint. The reverse pattern ("list scopes for
    document Y") is already covered by the primary key prefix.

    Both DDL statements use ``IF NOT EXISTS`` so the migration is
    idempotent — re-running on a database where the structures were
    created out-of-band is safe.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_scopes (
            document_id TEXT NOT NULL,
            scope_kind  TEXT NOT NULL,
            scope_ref   TEXT NOT NULL,
            added_at    TEXT NOT NULL,
            added_by    TEXT NOT NULL,
            PRIMARY KEY (document_id, scope_kind, scope_ref),
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_scopes_lookup "
        "ON document_scopes (scope_kind, scope_ref)"
    )


def _migrate_0010_taxonomy(conn: sqlite3.Connection) -> None:
    """Imposed taxonomy storage (ADR-017 + ADR-031, #379).

    Moves the operator-imposed taxonomy out of the
    ``KW_TAXONOMY_PATH`` YAML-only home into SQLite so it can be
    versioned, audited, and edited without redeploying. The YAML
    loader stays in place as a **bootstrap import** path
    (``POST /admin/taxonomy/import_yaml``) so existing operator
    workflows keep working.

    Two tables:

    * ``taxonomies`` — one row per published taxonomy version.
      ``active=1`` on exactly the most recently published row;
      ``publish`` flips the predecessor to ``active=0`` atomically.
      ``source`` records ``"yaml_import"`` (bootstrap) vs ``"api"``
      (admin-route publish) for audit traceability.
    * ``taxonomy_categories`` — flattened tree pinned to a taxonomy
      version. ``parent_id`` links a child to its parent; ``NULL``
      for a top-level category. ``sort_order`` preserves the order
      operators authored in the YAML or via the future API editor —
      the read path sorts by ``(parent_id, sort_order)``.

    The ``id`` column is the operator-stable category id
    (``hr.hybrid_work``) — same shape the YAML loader enforces. The
    composite primary key ``(taxonomy_id, id)`` allows the same
    category id to appear under different taxonomy versions
    simultaneously without conflict.

    Both DDL statements use ``IF NOT EXISTS`` so a re-run on a
    database that already has the tables is safe (no real-world
    deployments exist yet, but the convention matches every other
    migration).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS taxonomies (
            id              TEXT PRIMARY KEY,
            schema_version  TEXT NOT NULL,
            source          TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            created_by      TEXT NOT NULL,
            active          INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS taxonomy_categories (
            taxonomy_id     TEXT NOT NULL,
            id              TEXT NOT NULL,
            parent_id       TEXT,
            label           TEXT NOT NULL,
            description     TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (taxonomy_id, id),
            FOREIGN KEY (taxonomy_id) REFERENCES taxonomies(id) ON DELETE CASCADE
        )
        """
    )
    # Read pattern: "give me the active taxonomy" — common, single
    # row at most, justifies the partial index. SQLite supports
    # WHERE clauses on indexes, so this stays cheap.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_taxonomies_active ON taxonomies (active) WHERE active = 1"
    )
    # Read pattern: "give me the children of category X under
    # taxonomy T" — used by tree assembly on read.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_taxonomy_categories_parent "
        "ON taxonomy_categories (taxonomy_id, parent_id, sort_order)"
    )


# ---------------------------------------------------------------------------
# Ordered registry — append only, never renumber
# ---------------------------------------------------------------------------


def _migrate_0012_claims(conn: sqlite3.Connection) -> None:
    """Atomic Claim/Fact data model (ADR-031, #368).

    Adds the first-class subject–predicate–object atom alongside
    documents and chunks. Each row is a single assertion extracted
    from a validated version, with a pointer back to the chunks it
    was sourced from for evidence drill-down.

    Per ADR-031 ("SQLite is the truth for what was uploaded, parsed,
    validated, governed"), claims live here rather than in Neo4j —
    they are governance / audit data, not primary graph traversal
    data. Future contradiction detection / gap analysis consumers
    read from this table.

    Schema notes:

    * ``object_value`` and ``object_entity_id`` are mutually exclusive
      — exactly one is set per row. The Pydantic schema enforces the
      XOR; the DB schema is permissive (both nullable) so a future
      migration can relax the rule without a CHECK-constraint
      rewrite.
    * ``subject_entity_id`` is a soft reference to the entity-id
      convention used by ``app.services.knowledge.entity_extractor``
      (a deterministic ``entity-<sha256[:16]>`` hash). There is no
      centralised entities table today, so no FK is added — by
      contrast with ``version_id`` which has a real FK so cascade
      deletion of a document version cleans its claims.
    * ``provenance_chunk_ids_json`` stores the list of contributing
      chunk ids as a JSON-encoded string array. SQLite's JSON1 is
      sufficient for the v1 ad-hoc queries the read API exposes; a
      future migration can promote it to a join table without a
      contract change.
    * ``schema_version`` is recorded per-row so a future v0.2
      extractor can co-exist with v0.1 rows during a gradual
      re-extraction.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            id                          TEXT PRIMARY KEY,
            document_id                 TEXT NOT NULL,
            version_id                  TEXT NOT NULL,
            subject_entity_id           TEXT NOT NULL,
            predicate                   TEXT NOT NULL,
            object_value                TEXT,
            object_entity_id            TEXT,
            confidence                  REAL NOT NULL,
            schema_version              TEXT NOT NULL,
            extracted_at                TEXT NOT NULL,
            provenance_chunk_ids_json   TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES document_versions(id) ON DELETE CASCADE
        )
        """
    )
    # Read pattern: "every claim about subject X" — primary surface for
    # the contradiction-detection consumer (filed as the next slice).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_claims_subject_entity_id ON claims (subject_entity_id)"
    )
    # Read pattern: "all claims for version V" — used by cascade
    # deletion (delete_for_version) and the "what changed across
    # versions" diff consumer.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_version_id ON claims (version_id)")
    # Read pattern: "every claim with predicate P" — used by the
    # gap-analysis consumer ("find documents with no `is_a` claim").
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_predicate ON claims (predicate)")


def _migrate_0011_document_relations(conn: sqlite3.Connection) -> None:
    """Aggregated document↔document relation cache (ADR-031, #380).

    The Explorer's relation-evidence drawer (#318) calls
    ``GET /knowledge/relations/aggregate`` which today walks the
    Neo4j chunk-edge layer for every request. At the target catalog
    scale (100k+ chunks) that's a real cost on every render. This
    table caches the aggregate per (source, target) pair and is
    kept fresh by a recompute trigger fired on projection
    completion.

    Cache shape:

    * ``aggregate_score`` — max of contributing chunk-pair scores
      (matches the on-demand compute policy in
      :class:`KnowledgeRelationsService.explain_aggregate`).
    * ``pair_count`` — un-truncated total of contributing pairs
      (so the frontend's "+ N more" indicator stays accurate).
    * ``is_bridge`` / ``is_outlier`` — booleans (stored as INTEGER
      0/1 for SQLite portability; the read path coerces back).
    * ``top_pairs_json`` — JSON-encoded list of
      :class:`ContributingChunkPair` payloads; up to 100 entries
      stored (the route caps ``top_n`` at 100). Bigger ``top_n``
      requests fall through to the on-demand compute.
    * ``computed_at`` — ISO-8601 timestamp of when this row was
      written. The periodic sweep re-computes rows whose
      ``computed_at`` is older than the configured window.

    Two rows per pair (one per direction) — keeps the read path
    branchless and lets the route preserve the caller's
    ``(source, target)`` orientation in the response without
    re-orienting contributing pair fields.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_relations (
            source_document_id  TEXT NOT NULL,
            target_document_id  TEXT NOT NULL,
            aggregate_score     REAL NOT NULL,
            pair_count          INTEGER NOT NULL,
            is_bridge           INTEGER NOT NULL DEFAULT 0,
            is_outlier          INTEGER NOT NULL DEFAULT 0,
            top_pairs_json      TEXT NOT NULL,
            computed_at         TEXT NOT NULL,
            PRIMARY KEY (source_document_id, target_document_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_relations_source "
        "ON document_relations (source_document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_relations_target "
        "ON document_relations (target_document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_relations_computed_at "
        "ON document_relations (computed_at)"
    )


def _migrate_0013_processes(conn: sqlite3.Connection) -> None:
    """First-class Playbook/Process data model (#369, ADR-031).

    Procedural knowledge ("how do I do X") is governance-shaped — it
    belongs alongside ``documents`` / ``document_versions`` /
    ``semantic_documents`` in SQLite, not in the Neo4j graph layer.
    Per ADR-031 the source-of-truth split is "what was uploaded,
    parsed, validated, governed → SQLite; how does it relate →
    Neo4j"; a Process is governance about a document, so it lives
    in the catalog.

    Two tables:

    * ``processes`` — one row per extracted Process. ``document_id``
      and ``version_id`` link the Process back to the SOP it was
      extracted from so a re-extraction can replace the prior
      Process row deterministically. The ``version_id`` carries an
      ``ON DELETE CASCADE`` FK into ``document_versions(id)``
      (matches the convention established by ``0012_claims``) so
      purging a version cleans its Processes automatically.
      ``schema_version`` (matches :data:`PROCESS_SCHEMA_VERSION` in
      the schema module) gates future evolution: a v0.2 reader can
      refuse to deserialise a v0.1 row without ambiguity.
    * ``process_steps`` — one row per ordered step. ``preconditions_json``
      / ``outcomes_json`` / ``source_reference_ids_json`` are
      JSON-encoded ``list[str]`` columns (SQLite has no native array
      type; the store-layer round-trips through ``json.dumps`` /
      ``json.loads``). ``source_reference_ids`` carries the chunk
      ids the extractor used to derive the step — pre-locked here
      for AURA citation compatibility (ADR-029, #370). ``referenced_tool_id``
      is forward-compatible — there is no tools table today; the
      string is stored as-is so a future tool-calling integration
      (AURA #16) can light up without a schema migration.

    The compound primary key ``(process_id, step_number)`` keeps two
    steps from sharing a number within the same Process and gives the
    store layer the "ordered step rows for a process" read for free
    via ``ORDER BY step_number ASC``. ``ON DELETE CASCADE`` on the
    ``process_id`` foreign key means deleting the parent Process
    drops every step in one statement — used by
    ``ProcessStore.delete_for_version`` when a re-extraction needs to
    replace an existing Process atomically.

    Both indexes cover the hot read paths surfaced by the read API:

    * ``idx_processes_document_id`` — "list every process for this
      document family" (the audit / explorer view).
    * ``idx_processes_version_id`` — "every process produced by this
      version", which the future SOP-aware parser will hit on each
      re-extraction to invalidate stale Processes.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processes (
            id                  TEXT PRIMARY KEY,
            title               TEXT NOT NULL,
            document_id         TEXT NOT NULL,
            version_id          TEXT NOT NULL,
            schema_version      TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES document_versions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS process_steps (
            process_id                  TEXT NOT NULL,
            step_number                 INTEGER NOT NULL,
            title                       TEXT NOT NULL,
            body                        TEXT NOT NULL,
            preconditions_json          TEXT NOT NULL,
            outcomes_json               TEXT NOT NULL,
            referenced_tool_id          TEXT,
            source_reference_ids_json   TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (process_id, step_number),
            FOREIGN KEY (process_id) REFERENCES processes(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_processes_document_id ON processes (document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_processes_version_id ON processes (version_id)")


def _migrate_0014_document_topics(conn: sqlite3.Connection) -> None:
    """LLM-extracted document-level topic data model (#411, ADR-031).

    A :class:`~app.schemas.document_topic.DocumentTopic` is a
    document-level theme produced by the LLM
    :class:`~app.services.topic_extractor.TopicExtractor` — distinct
    from the deterministic chunk-cluster ``Topic`` that lives in the
    Neo4j graph layer (the latter is a graph node; this is governance
    / audit data per the ADR-031 split).

    One table, ``document_topics``. ``version_id`` carries an
    ``ON DELETE CASCADE`` FK into ``document_versions(id)`` (matches
    the convention established by ``0012_claims`` and ``0013_processes``)
    so purging a version cleans its topics automatically.
    ``supporting_chunk_ids_json`` is a JSON-encoded ``list[str]``
    column (SQLite has no native array type; the store-layer
    round-trips through ``json.dumps`` / ``json.loads``).
    ``keywords_json`` is the same shape for the topic's keyword
    list; both are pre-locked for v0.1 readability.

    ``schema_version`` (matches
    :data:`DOCUMENT_TOPIC_SCHEMA_VERSION` in the schema module)
    gates future evolution: a v0.2 reader can refuse to deserialise
    a v0.1 row without ambiguity.

    Two indexes cover the hot read paths surfaced by the read API:

    * ``idx_document_topics_document_id`` — "list every topic for
      this document family" (the Explorer / Atlas surface).
    * ``idx_document_topics_version_id`` — "every topic produced by
      this version", which the future TopicExtractor will hit on
      each re-extraction to invalidate stale topics.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_topics (
            id                          TEXT PRIMARY KEY,
            document_id                 TEXT NOT NULL,
            version_id                  TEXT NOT NULL,
            label                       TEXT NOT NULL,
            summary                     TEXT NOT NULL,
            keywords_json               TEXT NOT NULL DEFAULT '[]',
            confidence                  REAL NOT NULL,
            schema_version              TEXT NOT NULL,
            extracted_at                TEXT NOT NULL,
            supporting_chunk_ids_json   TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES document_versions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_topics_document_id "
        "ON document_topics (document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_topics_version_id ON document_topics (version_id)"
    )


def _migrate_0015_chunk_taxonomy_allocations(conn: sqlite3.Connection) -> None:
    """LLM business-taxonomy allocation per chunk (EPIC-1 slice 1.3, #340).

    A :class:`~app.schemas.chunk_taxonomy_allocation.ChunkTaxonomyAllocation`
    is one LLM pass over a single chunk that maps the chunk onto the
    operator-imposed business taxonomy (ADR-017 §3.5). Distinct from
    the deterministic chunk-cluster ``Topic`` that lives in the
    knowledge graph (auto-deduced clustering) and from the
    document-level ``DocumentTopic`` rows (LLM-extracted themes per
    document) — this slice carries chunk-level category assignments
    with per-assignment confidence and a SHA-256 fingerprint of the
    active taxonomy at allocation time.

    One table, ``chunk_taxonomy_allocations``. ``version_id`` carries
    an ``ON DELETE CASCADE`` FK into ``document_versions(id)``
    (matches the convention established by ``0012_claims`` /
    ``0013_processes`` / ``0014_document_topics``) so purging a
    version cleans its allocations automatically.
    ``assignments_json`` is a JSON-encoded array of
    :class:`BusinessCategoryAssignment` objects — SQLite has no
    native array type and a join table buys nothing the chunk-
    inspector UI cares about (it always loads every assignment for
    one chunk).

    ``taxonomy_fingerprint`` enables drift detection: an operator
    inspecting two allocation passes can group rows by fingerprint
    to see which were produced against which taxonomy snapshot.
    ``prompt_hash`` does the same for prompt evolution.

    Three indexes cover the hot read paths:

    * ``idx_chunk_taxonomy_allocations_document_id`` — "list every
      allocation for this document family" (the chunk-inspector
      panel's per-document view).
    * ``idx_chunk_taxonomy_allocations_chunk_id`` — "every
      allocation pass for this chunk" (the chunk-inspector's
      drill-down view).
    * ``idx_chunk_taxonomy_allocations_version_id`` — "every
      allocation produced by this version", which the re-allocation
      flow hits before each new pass to invalidate stale rows.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_taxonomy_allocations (
            id                          TEXT PRIMARY KEY,
            chunk_id                    TEXT NOT NULL,
            section_id                  TEXT NOT NULL,
            document_id                 TEXT NOT NULL,
            version_id                  TEXT NOT NULL,
            assignments_json            TEXT NOT NULL DEFAULT '[]',
            taxonomy_fingerprint        TEXT NOT NULL,
            model_id                    TEXT NOT NULL,
            prompt_hash                 TEXT NOT NULL,
            schema_version              TEXT NOT NULL,
            extracted_at                TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES document_versions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_taxonomy_allocations_document_id "
        "ON chunk_taxonomy_allocations (document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_taxonomy_allocations_chunk_id "
        "ON chunk_taxonomy_allocations (chunk_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_taxonomy_allocations_version_id "
        "ON chunk_taxonomy_allocations (version_id)"
    )


MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("0001_initial", _migrate_0001_initial),
    ("0002_add_review_columns", _migrate_0002_add_review_columns),
    ("0003_perf_indexes", _migrate_0003_perf_indexes),
    ("0004_document_scopes", _migrate_0004_document_scopes),
    ("0005_document_scopes_removed_at", _migrate_0005_document_scopes_removed_at),
    ("0006_documents_archived_at", _migrate_0006_documents_archived_at),
    ("0007_validation_metadata", _migrate_0007_validation_metadata),
    ("0008_corpus_norms", _migrate_0008_corpus_norms),
    ("0009_sampling_state", _migrate_0009_sampling_state),
    ("0010_taxonomy", _migrate_0010_taxonomy),
    ("0011_document_relations", _migrate_0011_document_relations),
    ("0012_claims", _migrate_0012_claims),
    ("0013_processes", _migrate_0013_processes),
    ("0014_document_topics", _migrate_0014_document_topics),
    ("0015_chunk_taxonomy_allocations", _migrate_0015_chunk_taxonomy_allocations),
]

# The set of table names that the legacy ``_initialize`` approach created.
# Used by the bootstrap detection to decide whether to stamp the
# bootstrap-eligible migrations without running them.
_LEGACY_TABLES = frozenset({"documents", "document_versions"})

# Migrations whose effects are guaranteed to already be present in any DB
# that triggers the legacy bootstrap path (i.e. the original ``_initialize``
# created these structures). New migrations added after the bootstrap rule
# was written must NOT be added here — they need to actually run on legacy
# databases too. Per audit #224, the perf indexes (0003) are intentionally
# not bootstrap-eligible because they did not exist before this commit.
_BOOTSTRAP_STAMPED_MIGRATION_IDS = frozenset(
    {
        "0001_initial",
        "0002_add_review_columns",
    }
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply every unapplied migration in order.

    This function is **not** wrapped in a transaction itself — each migration
    runs in its own savepoint so a failure in migration N does not roll back
    migrations 1..N-1 that already succeeded.

    Parameters
    ----------
    conn:
        An open :class:`sqlite3.Connection`.  The caller is responsible for
        the outer ``commit`` / ``rollback`` life-cycle (the
        :meth:`SQLiteCatalogStore._connect` context manager handles this).
    """
    # Ensure the tracking table exists.  This is idempotent and safe to run
    # on every startup.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )

    # Use positional index 0 (the id column) so this works regardless of
    # whether row_factory is set on the connection.
    applied: set[str] = {
        row[0] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }

    # --- Backwards-compatibility bootstrap --------------------------------
    # If schema_migrations is empty but the legacy tables already exist,
    # stamp the migrations whose effects are known to already be present
    # (``_BOOTSTRAP_STAMPED_MIGRATION_IDS``) without running their callables.
    # Any later migration drops through to the normal path so it actually
    # runs on the legacy database — required for additive migrations like
    # the perf indexes added by 0003 (audit #224).
    if not applied:
        existing_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if _LEGACY_TABLES.issubset(existing_tables):
            now = datetime.now(UTC).isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                [
                    (migration_id, now)
                    for migration_id, _ in MIGRATIONS
                    if migration_id in _BOOTSTRAP_STAMPED_MIGRATION_IDS
                ],
            )
            applied = set(_BOOTSTRAP_STAMPED_MIGRATION_IDS)

    # --- Normal path: run unapplied migrations in order -------------------
    for migration_id, migrate_fn in MIGRATIONS:
        if migration_id in applied:
            continue
        savepoint = f"sp_{migration_id}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            migrate_fn(conn)
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                (migration_id, now),
            )
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
