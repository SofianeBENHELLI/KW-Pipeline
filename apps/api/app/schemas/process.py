"""Pydantic schemas for the first-class Playbook/Process data model
(#369, ADR-031).

A Process captures the *procedural* shape of a SOP / playbook
document — ordered steps with preconditions and outcomes — that
flat chunk extraction loses. The downstream consumer (AURA
companion, Step 6) reads Processes directly to answer "how do I
do X" with a runnable sequence rather than quoted prose.

Per ADR-031, a Process is governance-shaped (it describes what was
extracted from a document) so it lives in SQLite alongside the
catalog tables, not in the Neo4j graph layer. The wire shape here
is the contract every persistence backend
(:class:`InMemoryProcessStore` / :class:`SQLiteProcessStore`)
round-trips and the read API
(``GET /knowledge/processes`` / ``GET /knowledge/processes/{id}``) returns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# Bumped when the wire shape of Process / ProcessStep changes.
# ``Literal`` so future evolution is gated: a v0.2 reader can refuse
# to deserialise a v0.1 row without ambiguity. The constant carries
# the matching ``Literal`` annotation so call-sites that assign it
# back to a ``ProcessSchemaVersion``-typed field don't need a cast.
ProcessSchemaVersion = Literal["v0.1"]
PROCESS_SCHEMA_VERSION: ProcessSchemaVersion = "v0.1"


class ProcessStep(BaseModel):
    """One ordered step inside a :class:`Process`.

    ``step_number`` is 1-indexed (matches how operators write
    "Step 1", "Step 2" in source SOPs) and unique within a Process.
    The compound PK ``(process_id, step_number)`` at the storage
    layer enforces that invariant; this schema enforces ``ge=1`` so
    a malformed payload never reaches the store.

    ``preconditions`` and ``outcomes`` are free-text bullets — the
    rule of thumb is "what must be true before this step can run"
    and "what is true after". Both default to empty lists rather
    than ``None`` because a step without preconditions is a real
    case (the first step typically has none) and the empty-list
    shape keeps the wire contract uniform for generated clients.

    ``referenced_tool_id`` is forward-compatible: there is no tools
    table today, so the field is just a free-text identifier the
    extractor can populate when a step explicitly says "invoke
    tool X". A future tool-calling integration (AURA #16) will
    join against a real ``tools`` table; until then, the string is
    stored as-is and treated as opaque.

    ``source_reference_ids`` carries the chunk ids the extractor
    used to derive this step. Pre-locked here for AURA citation
    compatibility (ADR-029): when the companion surfaces a
    ProcessStep as a citation source, it constructs one
    :class:`Citation` per id in this list. Defaults to empty for
    back-compat with extractors that haven't been updated yet; the
    SOP-aware parser (#390) populates it.
    """

    step_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    body: str
    preconditions: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    referenced_tool_id: str | None = None
    source_reference_ids: list[str] = Field(default_factory=list)


class ProcessSummary(BaseModel):
    """Metadata-only Process row for the list view.

    The full :class:`Process` payload includes ``steps``, which is
    unbounded in size. ``GET /knowledge/processes`` returns summaries (no
    steps) so the list response stays cheap even when the catalog
    holds large playbooks; consumers fetch a single Process via
    ``GET /knowledge/processes/{id}`` to get the ordered step bodies.
    """

    id: str
    title: str = Field(min_length=1, max_length=500)
    document_id: str
    version_id: str
    schema_version: ProcessSchemaVersion = PROCESS_SCHEMA_VERSION
    created_at: datetime


class Process(BaseModel):
    """Top-level Process — metadata + ordered steps.

    ``id`` is server-generated at extraction time. ``document_id``
    and ``version_id`` link the Process back to the SOP it was
    extracted from so a re-extraction can replace the prior Process
    row deterministically (see
    :meth:`app.services.process_store.ProcessStore.delete_for_version`).
    Field naming matches ``Citation`` / ``GroundedAnswer``
    (ADR-029) so a Process surfaced as a citation source needs no
    rename layer.

    ``steps`` is sorted by :attr:`ProcessStep.step_number` ASC on
    every read — both backends honour this ordering so consumers
    don't have to re-sort. An empty ``steps`` list is a valid shape
    (e.g. a Process whose extractor recognised the document as
    procedural but couldn't segment it) but the typical case is
    one or more steps.

    ``created_at`` is set server-side by the store on save; clients
    that pass a value have it overridden so the store remains the
    single source of truth for the timestamp.
    """

    id: str
    title: str = Field(min_length=1, max_length=500)
    document_id: str
    version_id: str
    schema_version: ProcessSchemaVersion = PROCESS_SCHEMA_VERSION
    steps: list[ProcessStep] = Field(default_factory=list)
    created_at: datetime


class ProcessListResponse(BaseModel):
    """Response shape for ``GET /knowledge/processes``.

    Mirrors the cursor-pagination envelope used by the rest of the
    knowledge surface: ``items`` carries the page; ``next_cursor``
    is opaque and ``None`` at end-of-stream. ``schema_version``
    pins the version of the Process wire shape so clients can
    reject incompatible payloads without inspecting individual
    items.
    """

    schema_version: ProcessSchemaVersion = PROCESS_SCHEMA_VERSION
    items: list[ProcessSummary] = Field(default_factory=list)
    next_cursor: str | None = None


__all__ = [
    "PROCESS_SCHEMA_VERSION",
    "Process",
    "ProcessListResponse",
    "ProcessSchemaVersion",
    "ProcessStep",
    "ProcessSummary",
]
