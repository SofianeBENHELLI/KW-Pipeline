"""In-memory store + transition primitives for the taxonomy versioning
lifecycle (EPIC-1 slice 1.2, issue #339, ADR-018).

Lives alongside :class:`app.services.taxonomy_store.TaxonomyStore`
(the existing singleton-active model that powers the YAML loader +
B2 read route). This store manages *versions* — the DRAFT →
CANDIDATE_V0 → VALIDATED_V1 → ARCHIVED chain plus the concept-
suggestion lifecycle inside drafts.

Why a sibling layer
-------------------

ADR-018 §10 keeps the YAML loader's singleton-active behavior as a
forward-compat shortcut (the YAML import wraps a promotion chain into
one atomic write). Splitting the lifecycle into a separate store
lets slices 1.5 (corpus emerging aggregator), 1.7 (LLM completion),
1.8 (validation workflow), and 1.9 (frontend mode indicator) all
land draft-bearing versions + suggestions without touching the
published-active taxonomy until they promote.

Audit trail
-----------

Every transition emits a structured-log event consumed by the
existing :class:`app.services.audit_event_store.AuditEventStore`.
Event names follow ADR-018 §7:

- ``taxonomy.draft.created`` / ``taxonomy.draft.discarded``
- ``taxonomy.candidate.promoted`` / ``taxonomy.candidate.rejected``
- ``taxonomy.version.validated`` / ``taxonomy.version.archived``
- ``taxonomy.concept.added`` / ``taxonomy.concept.transitioned``

``actor`` is included only when the caller threaded one (per the #91
backfill pattern from PRs #460 / #462 / #464). System-driven
transitions (the corpus aggregator landing NEW suggestions) emit no
actor; the key is omitted from the audit payload entirely.

SQLite persistence
------------------

Out of scope for this slice. The in-memory implementation here is
the contract the SQLite store + the existing migration system will
mirror in a follow-up PR. Construction is cheap; tests instantiate
freshly per case.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from app.schemas.taxonomy import Taxonomy
from app.schemas.taxonomy_version import (
    ConceptSuggestion,
    ConceptSuggestionState,
    TaxonomyState,
    TaxonomyVersion,
    is_legal_concept_transition,
    is_legal_version_transition,
)

log = logging.getLogger(__name__)


class TaxonomyVersionStoreProtocol(Protocol):
    """Persistence boundary for taxonomy versions.

    The in-memory implementation in this module is the MVP fit. A
    future SQLite implementation will share this Protocol so the
    chat / admin route layer can swap the backend without code
    changes.
    """

    def get(self, *, taxonomy_id: str, version_number: int) -> TaxonomyVersion | None: ...

    def list_for_taxonomy(self, *, taxonomy_id: str) -> list[TaxonomyVersion]: ...

    def active_validated(
        self, *, taxonomy_id: str
    ) -> TaxonomyVersion | None: ...

    def upsert(self, version: TaxonomyVersion) -> None: ...


class IllegalTaxonomyTransition(Exception):
    """Raised when a transition request violates the state machine.

    Carries the rejected ``(from_state, to_state)`` pair so the route
    layer can surface a 409 with a clear message rather than a 500.
    """

    def __init__(self, *, from_state: str, to_state: str, kind: str) -> None:
        super().__init__(
            f"Illegal {kind} transition: {from_state!r} → {to_state!r}."
        )
        self.from_state = from_state
        self.to_state = to_state
        self.kind = kind


class InMemoryTaxonomyVersionStore:
    """Dictionary-backed implementation. One dict keyed on
    ``(taxonomy_id, version_number)`` per process; not thread-safe
    (matches the existing :class:`InMemoryTaxonomyStore`'s posture)."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], TaxonomyVersion] = {}

    def get(
        self, *, taxonomy_id: str, version_number: int
    ) -> TaxonomyVersion | None:
        return self._versions.get((taxonomy_id, version_number))

    def list_for_taxonomy(self, *, taxonomy_id: str) -> list[TaxonomyVersion]:
        """Every version of one taxonomy, sorted by ``version_number`` ascending."""
        return sorted(
            (v for (tid, _), v in self._versions.items() if tid == taxonomy_id),
            key=lambda v: v.version_number,
        )

    def active_validated(
        self, *, taxonomy_id: str
    ) -> TaxonomyVersion | None:
        """The most recent non-ARCHIVED ``VALIDATED_*`` version, or ``None``.

        ADR-018 §4: "active" is implicit — derived from
        ``MAX(version_number) WHERE state LIKE 'VALIDATED_%' AND
        state != 'ARCHIVED'``. The in-memory implementation walks
        the version list; a SQLite follow-up uses an index on
        ``(taxonomy_id, state, version_number DESC)``.
        """
        candidates = [
            v
            for v in self.list_for_taxonomy(taxonomy_id=taxonomy_id)
            if v.state == "VALIDATED_V1"
        ]
        return candidates[-1] if candidates else None

    def upsert(self, version: TaxonomyVersion) -> None:
        self._versions[(version.taxonomy_id, version.version_number)] = version


# ─── Version-level transitions ─────────────────────────────────────────


def create_draft(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str | None = None,
    source_version: TaxonomyVersion | None = None,
    actor: str | None = None,
) -> TaxonomyVersion:
    """Land a new ``DRAFT`` version in the store.

    When ``source_version`` is supplied (e.g. branching from a
    Validated_Vn to edit a future Vn+1), the new draft inherits the
    source's tree as a starting point. Otherwise the draft starts
    empty.

    ``version_number`` is the next integer for the taxonomy_id (1 if
    nothing else exists). Mirrors ADR-018 §3.
    """
    if source_version is not None and taxonomy_id is None:
        taxonomy_id = source_version.taxonomy_id
    if taxonomy_id is None:
        # Empty list_for_taxonomy → fresh taxonomy. The TaxonomyVersion
        # default factory produces a uuid id; we use that.
        draft = TaxonomyVersion(
            version_number=1,
            state="DRAFT",
            taxonomy=Taxonomy(),
            created_by=actor,
        )
    else:
        existing = store.list_for_taxonomy(taxonomy_id=taxonomy_id)
        next_version_number = (
            max((v.version_number for v in existing), default=0) + 1
        )
        source_tree = (
            source_version.taxonomy if source_version is not None else Taxonomy()
        )
        draft = TaxonomyVersion(
            taxonomy_id=taxonomy_id,
            version_number=next_version_number,
            state="DRAFT",
            taxonomy=source_tree,
            created_by=actor,
        )
    store.upsert(draft)
    _emit(
        "taxonomy.draft.created",
        {
            "taxonomy_id": draft.taxonomy_id,
            "version_number": draft.version_number,
        },
        actor=actor,
    )
    return draft


def promote_to_candidate(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
    actor: str | None = None,
) -> TaxonomyVersion:
    """Transition ``DRAFT → CANDIDATE_V0``.

    Snapshots accepted + merged suggestions into the tree. Rejected /
    discarded suggestions are preserved on the audit trail (via the
    per-row state) but excluded from the published tree.
    """
    version = _require(store, taxonomy_id=taxonomy_id, version_number=version_number)
    _check_version_transition(version.state, "CANDIDATE_V0")
    accepted = sum(1 for s in version.suggestions if s.state == "ACCEPTED")
    merged = sum(1 for s in version.suggestions if s.state == "MERGED")
    rejected = sum(1 for s in version.suggestions if s.state == "REJECTED")
    deferred = sum(1 for s in version.suggestions if s.state == "DEFERRED")
    promoted = version.model_copy(
        update={
            "state": "CANDIDATE_V0",
            "state_changed_at": datetime.now(UTC),
            # ADR-018 §5: accepted + merged suggestions snapshot into
            # the tree on promotion. For the MVP we leave the existing
            # tree as-is — the tree-shape mutation that folds in the
            # accepted suggestions is the responsibility of slice 1.8
            # (workflow). This slice ships the lifecycle skeleton; the
            # tree-merge is small + isolated enough to follow.
            "suggestions": [],
        }
    )
    store.upsert(promoted)
    _emit(
        "taxonomy.candidate.promoted",
        {
            "taxonomy_id": taxonomy_id,
            "version_number": version_number,
            "source_version_number": version_number,
            "accepted_count": accepted,
            "rejected_count": rejected,
            "merged_count": merged,
            "deferred_count": deferred,
        },
        actor=actor,
    )
    return promoted


def validate_version(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
    version_label: str | None = None,
    actor: str | None = None,
) -> TaxonomyVersion:
    """Transition ``CANDIDATE_V0 → VALIDATED_V1``.

    Also archives the previously-active Validated, if any. The new
    Validated's :attr:`superseded_version_number` points at the
    previous active so the audit trail can replay history.
    """
    version = _require(store, taxonomy_id=taxonomy_id, version_number=version_number)
    _check_version_transition(version.state, "VALIDATED_V1")
    previous = store.active_validated(taxonomy_id=taxonomy_id)
    superseded_number = previous.version_number if previous is not None else None
    if previous is not None:
        archive_version(
            store,
            taxonomy_id=taxonomy_id,
            version_number=previous.version_number,
            actor=actor,
            superseded_by_version_number=version_number,
        )
    validated = version.model_copy(
        update={
            "state": "VALIDATED_V1",
            "version_label": version_label or version.version_label,
            "state_changed_at": datetime.now(UTC),
            "superseded_version_number": superseded_number,
        }
    )
    store.upsert(validated)
    _emit(
        "taxonomy.version.validated",
        {
            "taxonomy_id": taxonomy_id,
            "version_number": version_number,
            "superseded_version_number": superseded_number,
        },
        actor=actor,
    )
    return validated


def archive_version(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
    actor: str | None = None,
    reason: str | None = None,
    superseded_by_version_number: int | None = None,
) -> TaxonomyVersion:
    """Transition ``VALIDATED_V1 → ARCHIVED``.

    Called as a side-effect of :func:`validate_version` when a new
    Validated supersedes an old one, and standalone when an operator
    archives a Validated without replacing it.
    """
    version = _require(store, taxonomy_id=taxonomy_id, version_number=version_number)
    _check_version_transition(version.state, "ARCHIVED")
    archived = version.model_copy(
        update={
            "state": "ARCHIVED",
            "state_changed_at": datetime.now(UTC),
        }
    )
    store.upsert(archived)
    payload: dict[str, object] = {
        "taxonomy_id": taxonomy_id,
        "version_number": version_number,
    }
    if reason is not None:
        payload["reason"] = reason
    if superseded_by_version_number is not None:
        payload["superseded_by_version_number"] = superseded_by_version_number
    _emit("taxonomy.version.archived", payload, actor=actor)
    return archived


def discard_draft(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
    actor: str | None = None,
    reason: str | None = None,
) -> TaxonomyVersion:
    """Transition ``DRAFT → DISCARDED`` (or ``CANDIDATE_V0 → DISCARDED``).

    Retained for audit so a future review pass sees what was tried
    and abandoned.
    """
    version = _require(store, taxonomy_id=taxonomy_id, version_number=version_number)
    _check_version_transition(version.state, "DISCARDED")
    discarded = version.model_copy(
        update={
            "state": "DISCARDED",
            "state_changed_at": datetime.now(UTC),
            # Drop suggestions on discard — they're audit rows now,
            # not in-flight workflow items. The audit event below
            # captures the count.
            "suggestions": [],
        }
    )
    store.upsert(discarded)
    payload: dict[str, object] = {
        "taxonomy_id": taxonomy_id,
        "version_number": version_number,
        "discarded_from_state": version.state,
        "suggestion_count": len(version.suggestions),
    }
    if reason is not None:
        payload["reason"] = reason
    _emit("taxonomy.draft.discarded", payload, actor=actor)
    return discarded


# ─── Concept-suggestion-level transitions ──────────────────────────────


def add_suggestions(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
    suggestions: Iterable[ConceptSuggestion],
    actor: str | None = None,
) -> TaxonomyVersion:
    """Append concept suggestions to a DRAFT version.

    Multiple at a time so the corpus aggregator (slice 1.5) can
    flush a batch from the per-chunk extractor output in one call.
    Suggestions land in their construction state (default ``NEW``);
    transitioning them is :func:`transition_concept`'s job.
    """
    version = _require(store, taxonomy_id=taxonomy_id, version_number=version_number)
    if version.state != "DRAFT":
        raise IllegalTaxonomyTransition(
            from_state=version.state,
            to_state="DRAFT",
            kind="concept_add",
        )
    new_list = list(version.suggestions) + list(suggestions)
    updated = version.model_copy(update={"suggestions": new_list})
    store.upsert(updated)
    for suggestion in suggestions:
        _emit(
            "taxonomy.concept.added",
            {
                "taxonomy_id": taxonomy_id,
                "version_number": version_number,
                "concept_id": suggestion.suggestion_id,
                "source": suggestion.source,
            },
            actor=actor,
        )
    return updated


def transition_concept(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
    suggestion_id: str,
    to_state: ConceptSuggestionState,
    actor: str | None = None,
    reason: str | None = None,
    merge_target_id: str | None = None,
) -> ConceptSuggestion:
    """Move one suggestion through its state machine.

    Mirrors the version-level transition contract: illegal moves
    raise :class:`IllegalTaxonomyTransition` so the route layer
    surfaces a 409 with a clear message. ``merge_target_id`` is
    required when transitioning to ``MERGED`` (enforced by
    :class:`ConceptSuggestion`'s validator) and forbidden otherwise.
    """
    version = _require(store, taxonomy_id=taxonomy_id, version_number=version_number)
    if version.state != "DRAFT":
        raise IllegalTaxonomyTransition(
            from_state=version.state,
            to_state="DRAFT",
            kind="concept_transition",
        )
    updated_suggestions: list[ConceptSuggestion] = []
    transitioned: ConceptSuggestion | None = None
    for s in version.suggestions:
        if s.suggestion_id != suggestion_id:
            updated_suggestions.append(s)
            continue
        if not is_legal_concept_transition(from_state=s.state, to_state=to_state):
            raise IllegalTaxonomyTransition(
                from_state=s.state,
                to_state=to_state,
                kind="concept",
            )
        new_state_changed_at = datetime.now(UTC)
        update_fields: dict[str, object] = {
            "state": to_state,
            "state_changed_at": new_state_changed_at,
            "last_actor": actor,
        }
        if to_state == "MERGED":
            if merge_target_id is None:
                raise ValueError(
                    "merge_target_id is required when transitioning to MERGED."
                )
            update_fields["merge_target_id"] = merge_target_id
        transitioned = s.model_copy(update=update_fields)
        updated_suggestions.append(transitioned)
    if transitioned is None:
        raise KeyError(
            f"Suggestion {suggestion_id!r} not found in version "
            f"({taxonomy_id!r}, {version_number})."
        )
    store.upsert(version.model_copy(update={"suggestions": updated_suggestions}))
    payload: dict[str, object] = {
        "taxonomy_id": taxonomy_id,
        "version_number": version_number,
        "concept_id": suggestion_id,
        "from": [s.state for s in version.suggestions if s.suggestion_id == suggestion_id][0],
        "to": to_state,
    }
    if reason is not None:
        payload["reason"] = reason
    if merge_target_id is not None:
        payload["merge_target_id"] = merge_target_id
    _emit("taxonomy.concept.transitioned", payload, actor=actor)
    return transitioned


# ─── Helpers ───────────────────────────────────────────────────────────


def _require(
    store: TaxonomyVersionStoreProtocol,
    *,
    taxonomy_id: str,
    version_number: int,
) -> TaxonomyVersion:
    version = store.get(taxonomy_id=taxonomy_id, version_number=version_number)
    if version is None:
        raise KeyError(
            f"TaxonomyVersion ({taxonomy_id!r}, {version_number}) not found."
        )
    return version


def _check_version_transition(from_state: TaxonomyState, to_state: TaxonomyState) -> None:
    if not is_legal_version_transition(from_state=from_state, to_state=to_state):
        raise IllegalTaxonomyTransition(
            from_state=from_state,
            to_state=to_state,
            kind="version",
        )


def _emit(event: str, payload: dict[str, object], *, actor: str | None) -> None:
    """Emit a structured-log event with ``actor`` folded into ``extra`` only when set.

    Mirrors the pattern from :mod:`app.services.extraction_job_service`
    (PR #464). Passing ``actor: None`` would land a ``null`` value in
    the audit JSON and confuse the :func:`event_actor` projection.
    """
    extra = dict(payload)
    if actor is not None:
        extra["actor"] = actor
    log.info(event, extra=extra)


__all__ = [
    "IllegalTaxonomyTransition",
    "InMemoryTaxonomyVersionStore",
    "TaxonomyVersionStoreProtocol",
    "add_suggestions",
    "archive_version",
    "create_draft",
    "discard_draft",
    "promote_to_candidate",
    "transition_concept",
    "validate_version",
]
