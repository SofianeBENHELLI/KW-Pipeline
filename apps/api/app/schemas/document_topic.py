"""Pydantic schemas for the LLM-extracted document-topic data model
(#411, ADR-031).

A :class:`DocumentTopic` is a *document-level theme* — the LLM's
answer to "what is this document about?". It is intentionally
distinct from the existing graph-side ``Topic`` (which is a
chunk-cluster derived from the deterministic
:class:`~app.services.knowledge.topic_clustering.TopicClusteringService`
and lives in the knowledge graph as a node). The two co-exist:

* Graph ``Topic`` → still produced by the deterministic clustering;
  feeds graph layout, ``same_topic_as`` edges, bridge / outlier
  detection (ADR-028 §3).
* :class:`DocumentTopic` → produced by the LLM
  :class:`~app.services.topic_extractor.TopicExtractor`; surfaces in
  the operator-facing topic UX (Explorer search Topics group, Atlas
  corpus summary, future Orbital reviewer panel).

Per ADR-031 the persistence boundary is SQLite — topic themes are
governance / audit data, not graph traversal data. The wire shape
here is what both the in-memory test fake and the SQLite store
round-trip; the store layer never invents fields the wire model
doesn't carry.

Field invariants enforced here (not the DB schema):

* ``schema_version`` is a frozen ``Literal["v0.1"]``. Future
  evolution lands a new literal value and the store / extractor
  paths update in lock-step; the wire is gated so v0.1 readers
  never silently parse a v0.2 payload.
* ``supporting_chunk_ids`` is non-empty — a topic with no provenance
  is unverifiable and the extractor must skip it (mirrors the same
  posture in :class:`~app.schemas.claim.Claim.provenance_chunk_ids`).
* ``confidence`` lives in ``[0, 1]`` so consumers can apply a single
  threshold across topics, claims, and entities.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# Bumped when the wire shape of a DocumentTopic changes. The SQLite
# store records this per-row so a future v0.2 extractor can co-exist
# with v0.1 rows during a gradual re-extraction.
DocumentTopicSchemaVersion = Literal["v0.1"]
DOCUMENT_TOPIC_SCHEMA_VERSION: DocumentTopicSchemaVersion = "v0.1"


class DocumentTopic(BaseModel):
    """One LLM-extracted document-level theme.

    ``label`` is a short human-readable name (e.g. ``"Microservices
    architecture"``); ``summary`` is one or two sentences explaining
    what the theme covers in this document. Both fields are bounded
    so the v1 read API can render them inline without truncation
    games on the consumer side.

    ``keywords`` is a small list of single-word identifiers (3–8 in
    practice) that the extractor associates with the theme. The
    schema doesn't enforce an upper bound — the extractor is the
    right place to police the size, and trimming on read would
    silently lose data.

    ``supporting_chunk_ids`` is the list of section / chunk ids the
    theme was sourced from. Non-empty by Pydantic ``min_length=1`` —
    a theme without provenance is unverifiable and the extractor
    must skip it. Stored on disk as a JSON-encoded array per ADR-031
    (SQLite is the truth; the JSON column avoids a N:M join table
    for the v1 read API).

    ``extracted_at`` is set server-side by the store on save — the
    extractor hands the topic in without it (the operator workflow
    has no notion of "when did the LLM run"). The store's
    ``save_topics`` populates it before INSERT.
    """

    id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    schema_version: DocumentTopicSchemaVersion = DOCUMENT_TOPIC_SCHEMA_VERSION
    extracted_at: datetime
    supporting_chunk_ids: list[str] = Field(min_length=1)


class DocumentTopicsListResponse(BaseModel):
    """Response envelope for ``GET /knowledge/topics``.

    ``next_cursor`` follows the same opaque-cursor pattern as the
    rest of the catalog read paths — the codec lives in
    :mod:`app.services.catalog_store` and clients must treat the
    string as opaque.
    """

    schema_version: DocumentTopicSchemaVersion = DOCUMENT_TOPIC_SCHEMA_VERSION
    items: list[DocumentTopic] = Field(default_factory=list)
    next_cursor: str | None = None


__all__ = [
    "DOCUMENT_TOPIC_SCHEMA_VERSION",
    "DocumentTopic",
    "DocumentTopicSchemaVersion",
    "DocumentTopicsListResponse",
]
