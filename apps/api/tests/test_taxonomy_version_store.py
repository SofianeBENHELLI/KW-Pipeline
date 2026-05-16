"""Tests for the taxonomy versioning lifecycle (EPIC-1 §1.2, ADR-018).

Three layers:

1. **Schema invariants** — Pydantic-level pin on
   :class:`ConceptSuggestion` and :class:`TaxonomyVersion` (merge_target
   coupling, draft-only suggestions, transition-legality predicates).
2. **Store CRUD** — :class:`InMemoryTaxonomyVersionStore` get / list /
   ``active_validated`` semantics.
3. **Lifecycle transitions** — every legal transition + a handful of
   the rejection paths. Audit-event emission is asserted via
   ``caplog`` so the actor-id threading lands at the structured-log
   boundary (the SQLite audit store consumes the same records via
   ``AuditLogHandler``).
"""

from __future__ import annotations

import logging
import re

import pytest

from app.schemas.taxonomy import Taxonomy, TaxonomyCategory
from app.schemas.taxonomy_version import (
    ConceptSuggestion,
    TaxonomyVersion,
    is_legal_concept_transition,
    is_legal_version_transition,
)
from app.services.taxonomy_version_store import (
    IllegalTaxonomyTransition,
    InMemoryTaxonomyVersionStore,
    add_suggestions,
    archive_version,
    create_draft,
    discard_draft,
    promote_to_candidate,
    transition_concept,
    validate_version,
)

# ─── Schema invariants ─────────────────────────────────────────────────


class TestConceptSuggestionInvariants:
    def test_merge_target_only_when_merged(self) -> None:
        with pytest.raises(ValueError, match="merge_target_id"):
            ConceptSuggestion(
                label="Concept",
                description="Body",
                state="ACCEPTED",
                merge_target_id="cat-x",
            )

    def test_merged_requires_merge_target(self) -> None:
        with pytest.raises(ValueError, match="merge_target_id"):
            ConceptSuggestion(
                label="Concept",
                description="Body",
                state="MERGED",
            )

    def test_minimum_label_length(self) -> None:
        with pytest.raises(ValueError):
            ConceptSuggestion(label="", description="ok")


class TestTaxonomyVersionInvariants:
    def test_suggestions_only_on_drafts(self) -> None:
        with pytest.raises(ValueError, match="DRAFT versions"):
            TaxonomyVersion(
                version_number=1,
                state="VALIDATED_V1",
                suggestions=[
                    ConceptSuggestion(label="x", description="y"),
                ],
            )

    def test_drafts_may_carry_suggestions(self) -> None:
        v = TaxonomyVersion(
            version_number=1,
            state="DRAFT",
            suggestions=[ConceptSuggestion(label="x", description="y")],
        )
        assert len(v.suggestions) == 1


class TestTransitionPredicates:
    @pytest.mark.parametrize(
        "from_state,to_state,legal",
        [
            ("DRAFT", "CANDIDATE_V0", True),
            ("DRAFT", "DISCARDED", True),
            ("DRAFT", "VALIDATED_V1", False),
            ("CANDIDATE_V0", "VALIDATED_V1", True),
            ("CANDIDATE_V0", "DRAFT", False),
            ("VALIDATED_V1", "ARCHIVED", True),
            ("VALIDATED_V1", "DRAFT", False),
            ("ARCHIVED", "VALIDATED_V1", False),
            ("DISCARDED", "DRAFT", False),
        ],
    )
    def test_version_transitions(self, from_state: str, to_state: str, legal: bool) -> None:
        assert is_legal_version_transition(from_state=from_state, to_state=to_state) is legal

    @pytest.mark.parametrize(
        "from_state,to_state,legal",
        [
            ("NEW", "ACCEPTED", True),
            ("NEW", "UNDER_REVIEW", True),
            ("UNDER_REVIEW", "ACCEPTED", True),
            ("UNDER_REVIEW", "MERGED", True),
            ("ACCEPTED", "REJECTED", False),
            ("REJECTED", "NEW", False),
            ("DEFERRED", "UNDER_REVIEW", True),
        ],
    )
    def test_concept_transitions(self, from_state: str, to_state: str, legal: bool) -> None:
        assert is_legal_concept_transition(from_state=from_state, to_state=to_state) is legal


# ─── Store CRUD ────────────────────────────────────────────────────────


class TestStoreCRUD:
    def test_get_returns_none_when_missing(self) -> None:
        store = InMemoryTaxonomyVersionStore()
        assert store.get(taxonomy_id="x", version_number=1) is None

    def test_upsert_and_get_round_trip(self) -> None:
        store = InMemoryTaxonomyVersionStore()
        version = TaxonomyVersion(taxonomy_id="tax-1", version_number=1, state="DRAFT")
        store.upsert(version)
        fetched = store.get(taxonomy_id="tax-1", version_number=1)
        assert fetched is not None
        assert fetched.taxonomy_id == "tax-1"
        assert fetched.state == "DRAFT"

    def test_list_returns_ordered_by_version_number(self) -> None:
        store = InMemoryTaxonomyVersionStore()
        for n in (2, 1, 3):
            store.upsert(TaxonomyVersion(taxonomy_id="tax-x", version_number=n, state="DRAFT"))
        listed = store.list_for_taxonomy(taxonomy_id="tax-x")
        assert [v.version_number for v in listed] == [1, 2, 3]

    def test_active_validated_returns_highest_unarchived(self) -> None:
        store = InMemoryTaxonomyVersionStore()
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=1, state="ARCHIVED"))
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=2, state="VALIDATED_V1"))
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=3, state="DRAFT"))
        active = store.active_validated(taxonomy_id="t")
        assert active is not None
        assert active.version_number == 2

    def test_active_validated_none_when_only_drafts_or_archives(self) -> None:
        store = InMemoryTaxonomyVersionStore()
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=1, state="ARCHIVED"))
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=2, state="DRAFT"))
        assert store.active_validated(taxonomy_id="t") is None


# ─── Lifecycle transitions ─────────────────────────────────────────────


@pytest.fixture
def store() -> InMemoryTaxonomyVersionStore:
    return InMemoryTaxonomyVersionStore()


def _records(caplog: pytest.LogCaptureFixture, event_name: str):
    return [r for r in caplog.records if r.msg == event_name]


def _extra(record: logging.LogRecord) -> dict:
    reserved = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
        "message",
        "asctime",
    }
    return {k: v for k, v in vars(record).items() if k not in reserved}


class TestCreateDraft:
    def test_first_draft_for_new_taxonomy_is_version_1(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        draft = create_draft(store, actor="ada")
        assert draft.state == "DRAFT"
        assert draft.version_number == 1
        assert draft.taxonomy_id  # uuid set
        assert draft.created_by == "ada"
        events = _records(caplog, "taxonomy.draft.created")
        assert events and _extra(events[-1]).get("actor") == "ada"

    def test_subsequent_draft_branches_from_source(
        self, store: InMemoryTaxonomyVersionStore
    ) -> None:
        validated = TaxonomyVersion(
            taxonomy_id="tax-y",
            version_number=1,
            state="VALIDATED_V1",
            taxonomy=Taxonomy(
                categories=[
                    TaxonomyCategory(id="hr", label="HR", description="People."),
                ]
            ),
        )
        store.upsert(validated)
        draft = create_draft(store, source_version=validated, actor="bob")
        assert draft.taxonomy_id == "tax-y"
        assert draft.version_number == 2
        assert draft.taxonomy.categories[0].id == "hr"

    def test_created_event_omits_actor_when_system(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        create_draft(store)
        events = _records(caplog, "taxonomy.draft.created")
        assert events and not hasattr(events[-1], "actor")


class TestPromote:
    def test_draft_promotes_to_candidate(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        draft = create_draft(store, actor="ada")
        caplog.set_level(logging.INFO)
        promoted = promote_to_candidate(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            actor="ada",
        )
        assert promoted.state == "CANDIDATE_V0"
        events = _records(caplog, "taxonomy.candidate.promoted")
        assert events and _extra(events[-1]).get("actor") == "ada"

    def test_promote_from_validated_is_illegal(self, store: InMemoryTaxonomyVersionStore) -> None:
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=1, state="VALIDATED_V1"))
        with pytest.raises(IllegalTaxonomyTransition) as exc:
            promote_to_candidate(store, taxonomy_id="t", version_number=1)
        assert exc.value.kind == "version"


class TestValidate:
    def test_candidate_validates_and_supersedes_previous(
        self, store: InMemoryTaxonomyVersionStore
    ) -> None:
        # Land an existing Validated.
        existing = TaxonomyVersion(
            taxonomy_id="tax-z",
            version_number=1,
            state="VALIDATED_V1",
        )
        store.upsert(existing)
        # Build a fresh draft → candidate for the same taxonomy.
        draft = create_draft(store, taxonomy_id="tax-z")
        promoted = promote_to_candidate(
            store, taxonomy_id="tax-z", version_number=draft.version_number
        )
        validated = validate_version(
            store, taxonomy_id="tax-z", version_number=promoted.version_number
        )
        assert validated.state == "VALIDATED_V1"
        assert validated.superseded_version_number == 1
        # Previous Validated is now archived.
        prior = store.get(taxonomy_id="tax-z", version_number=1)
        assert prior is not None
        assert prior.state == "ARCHIVED"
        # Active validated reflects the new one.
        active = store.active_validated(taxonomy_id="tax-z")
        assert active is not None and active.version_number == validated.version_number

    def test_validate_records_version_label(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        promote_to_candidate(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
        )
        validated = validate_version(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            version_label="V1",
        )
        assert validated.version_label == "V1"

    def test_validate_from_draft_is_illegal(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        with pytest.raises(IllegalTaxonomyTransition):
            validate_version(
                store,
                taxonomy_id=draft.taxonomy_id,
                version_number=draft.version_number,
            )


class TestArchive:
    def test_archive_validated_emits_event(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=1, state="VALIDATED_V1"))
        caplog.set_level(logging.INFO)
        archived = archive_version(
            store,
            taxonomy_id="t",
            version_number=1,
            actor="ada",
            reason="superseded by V2",
        )
        assert archived.state == "ARCHIVED"
        events = _records(caplog, "taxonomy.version.archived")
        assert events and _extra(events[-1]).get("reason") == "superseded by V2"

    def test_archive_draft_is_illegal(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        with pytest.raises(IllegalTaxonomyTransition):
            archive_version(
                store,
                taxonomy_id=draft.taxonomy_id,
                version_number=draft.version_number,
            )


class TestDiscard:
    def test_draft_discards_cleanly(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        draft = create_draft(store)
        caplog.set_level(logging.INFO)
        discarded = discard_draft(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            actor="ada",
            reason="exploratory session",
        )
        assert discarded.state == "DISCARDED"
        assert discarded.suggestions == []
        events = _records(caplog, "taxonomy.draft.discarded")
        assert events and _extra(events[-1]).get("discarded_from_state") == "DRAFT"

    def test_candidate_discards_too(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        promote_to_candidate(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
        )
        discarded = discard_draft(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
        )
        assert discarded.state == "DISCARDED"

    def test_discard_from_validated_is_illegal(self, store: InMemoryTaxonomyVersionStore) -> None:
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=1, state="VALIDATED_V1"))
        with pytest.raises(IllegalTaxonomyTransition):
            discard_draft(store, taxonomy_id="t", version_number=1)


# ─── Concept suggestions ───────────────────────────────────────────────


class TestAddSuggestions:
    def test_appends_to_draft(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        draft = create_draft(store)
        caplog.set_level(logging.INFO)
        suggestions = [
            ConceptSuggestion(
                label="Battery Thermal",
                description="Thermal management for batteries.",
                source="extractor",
            ),
            ConceptSuggestion(
                label="Engineering Change",
                description="Engineering change request workflow.",
                source="llm",
            ),
        ]
        updated = add_suggestions(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestions=suggestions,
            actor="bob",
        )
        assert len(updated.suggestions) == 2
        events = _records(caplog, "taxonomy.concept.added")
        assert len(events) == 2
        assert {_extra(e).get("source") for e in events} == {"extractor", "llm"}
        assert all(_extra(e).get("actor") == "bob" for e in events)

    def test_rejects_non_draft_targets(self, store: InMemoryTaxonomyVersionStore) -> None:
        store.upsert(TaxonomyVersion(taxonomy_id="t", version_number=1, state="VALIDATED_V1"))
        with pytest.raises(IllegalTaxonomyTransition):
            add_suggestions(
                store,
                taxonomy_id="t",
                version_number=1,
                suggestions=[ConceptSuggestion(label="x", description="y")],
            )


class TestTransitionConcept:
    def test_accept_transition(
        self, store: InMemoryTaxonomyVersionStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        draft = create_draft(store)
        suggestion = ConceptSuggestion(label="Concept", description="Body.")
        add_suggestions(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestions=[suggestion],
        )
        caplog.set_level(logging.INFO)
        transitioned = transition_concept(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestion_id=suggestion.suggestion_id,
            to_state="ACCEPTED",
            actor="ada",
        )
        assert transitioned.state == "ACCEPTED"
        events = _records(caplog, "taxonomy.concept.transitioned")
        assert events and _extra(events[-1]).get("to") == "ACCEPTED"

    def test_merge_requires_target(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        suggestion = ConceptSuggestion(label="X", description="Y")
        add_suggestions(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestions=[suggestion],
        )
        # ``MERGED`` is only legal from ``UNDER_REVIEW`` per ADR-018 §5;
        # transition through there first so the merge_target_id check
        # is the one that fires.
        transition_concept(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestion_id=suggestion.suggestion_id,
            to_state="UNDER_REVIEW",
        )
        with pytest.raises(ValueError, match="merge_target_id is required"):
            transition_concept(
                store,
                taxonomy_id=draft.taxonomy_id,
                version_number=draft.version_number,
                suggestion_id=suggestion.suggestion_id,
                to_state="MERGED",
            )

    def test_merge_with_target_succeeds(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        suggestion = ConceptSuggestion(label="X", description="Y")
        add_suggestions(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestions=[suggestion],
        )
        # NEW → UNDER_REVIEW → MERGED (the canonical path per ADR-018).
        transition_concept(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestion_id=suggestion.suggestion_id,
            to_state="UNDER_REVIEW",
        )
        merged = transition_concept(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestion_id=suggestion.suggestion_id,
            to_state="MERGED",
            merge_target_id="hr.existing",
        )
        assert merged.state == "MERGED"
        assert merged.merge_target_id == "hr.existing"

    def test_illegal_concept_transition_raises(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        suggestion = ConceptSuggestion(label="X", description="Y", state="ACCEPTED")
        add_suggestions(
            store,
            taxonomy_id=draft.taxonomy_id,
            version_number=draft.version_number,
            suggestions=[suggestion],
        )
        with pytest.raises(IllegalTaxonomyTransition):
            transition_concept(
                store,
                taxonomy_id=draft.taxonomy_id,
                version_number=draft.version_number,
                suggestion_id=suggestion.suggestion_id,
                to_state="REJECTED",
            )

    def test_unknown_suggestion_raises_keyerror(self, store: InMemoryTaxonomyVersionStore) -> None:
        draft = create_draft(store)
        with pytest.raises(KeyError, match=re.compile(r"missing", re.IGNORECASE)):
            transition_concept(
                store,
                taxonomy_id=draft.taxonomy_id,
                version_number=draft.version_number,
                suggestion_id="missing",
                to_state="ACCEPTED",
            )


# ─── End-to-end happy-path ─────────────────────────────────────────────


def test_full_lifecycle_draft_to_validated_and_archived(
    store: InMemoryTaxonomyVersionStore,
) -> None:
    """One taxonomy, two complete promotion cycles. Pins that the
    audit-trail-relevant fields (``superseded_version_number``,
    archive transition of the previous Validated) all line up."""
    # Cycle 1: empty corpus → first Validated_V1.
    draft1 = create_draft(store, actor="ada")
    add_suggestions(
        store,
        taxonomy_id=draft1.taxonomy_id,
        version_number=draft1.version_number,
        suggestions=[
            ConceptSuggestion(label="Battery", description="Battery domain."),
        ],
        actor="ada",
    )
    promote_to_candidate(
        store,
        taxonomy_id=draft1.taxonomy_id,
        version_number=draft1.version_number,
        actor="ada",
    )
    v1 = validate_version(
        store,
        taxonomy_id=draft1.taxonomy_id,
        version_number=draft1.version_number,
        version_label="V1",
        actor="ada",
    )
    assert v1.state == "VALIDATED_V1"
    assert v1.superseded_version_number is None

    # Cycle 2: branch a new draft from V1, promote, validate → V2.
    draft2 = create_draft(store, source_version=v1, actor="bob")
    assert draft2.version_number == 2
    promote_to_candidate(
        store,
        taxonomy_id=draft2.taxonomy_id,
        version_number=draft2.version_number,
        actor="bob",
    )
    v2 = validate_version(
        store,
        taxonomy_id=draft2.taxonomy_id,
        version_number=draft2.version_number,
        version_label="V2",
        actor="bob",
    )
    assert v2.state == "VALIDATED_V1"  # state name is V1; version_number is 2
    assert v2.version_number == 2
    assert v2.superseded_version_number == 1
    # V1 is archived as a side-effect.
    archived = store.get(taxonomy_id=draft1.taxonomy_id, version_number=1)
    assert archived is not None
    assert archived.state == "ARCHIVED"
    # active_validated returns V2.
    active = store.active_validated(taxonomy_id=draft1.taxonomy_id)
    assert active is not None and active.version_number == 2
