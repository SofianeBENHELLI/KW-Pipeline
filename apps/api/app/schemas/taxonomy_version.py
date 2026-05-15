"""Taxonomy versioning lifecycle types (EPIC-1 slice 1.2, issue #339).

Implements the wire shape from ADR-018: a ``TaxonomyVersion`` carries
the existing :class:`Taxonomy` tree plus the additive identity +
state-machine fields the lifecycle needs. Concept suggestions inside
a draft live on a sibling :class:`ConceptSuggestion` model.

Why a sibling type instead of widening :class:`Taxonomy`
----------------------------------------------------------

The existing :class:`Taxonomy` is the published wire shape consumed by
the Explorer left rail + the classifier. Widening it would force
every consumer to handle the lifecycle fields. A sibling
:class:`TaxonomyVersion` lets ADR-018's versioning landing wrap the
existing shape: ``TaxonomyVersion.taxonomy`` carries the tree, and
the lifecycle metadata rides on the wrapper.

When slices 1.7 / 1.8 wire the admin transition routes, they'll
serve :class:`TaxonomyVersion`; the public read route in B2 stays on
:class:`Taxonomy` (the active validated version's tree) so no
existing consumer breaks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from app.schemas import APISchemaModel as BaseModel
from app.schemas.taxonomy import Taxonomy

TAXONOMY_VERSION_SCHEMA_VERSION: Final[Literal["v0.1"]] = "v0.1"


# ─── State machines ────────────────────────────────────────────────────


TaxonomyState = Literal[
    "DRAFT",
    "CANDIDATE_V0",
    "VALIDATED_V1",
    "ARCHIVED",
    "DISCARDED",
]
"""Lifecycle states for a whole :class:`TaxonomyVersion` (ADR-018 §2).

``VALIDATED_V1`` is the canonical promoted form; subsequent
validations promote into the same Literal value but with an
incremented :attr:`TaxonomyVersion.version_number` (the integer is
the audit-trail key, not the state name). The label
:attr:`TaxonomyVersion.version_label` is the free-text display form
(``"V2"``, ``"2026-Q3"``, …).
"""


ConceptSuggestionState = Literal[
    "NEW",
    "UNDER_REVIEW",
    "ACCEPTED",
    "REJECTED",
    "MERGED",
    "DEFERRED",
]
"""Lifecycle states for one concept suggestion inside a DRAFT version
(ADR-018 §5)."""


ConceptSuggestionSource = Literal[
    "extractor",
    "llm",
    "operator",
]
"""Where a suggestion came from. The deterministic extractor (slice
1.1, #338) tags itself ``extractor``; the LLM completion path (slice
1.7) tags itself ``llm``; operator-authored additions in the admin
UI tag themselves ``operator`` so the audit feed can attribute the
source of new candidates."""


# Transition tables — module-level constants so a future test can
# assert exhaustiveness without scraping a state machine library.
# Empty list means "terminal".
_ALLOWED_VERSION_TRANSITIONS: dict[
    str, tuple[str, ...]
] = {
    "DRAFT": ("CANDIDATE_V0", "DISCARDED"),
    "CANDIDATE_V0": ("VALIDATED_V1", "DISCARDED"),
    "VALIDATED_V1": ("ARCHIVED",),
    "ARCHIVED": (),
    "DISCARDED": (),
}

_ALLOWED_CONCEPT_TRANSITIONS: dict[
    str, tuple[str, ...]
] = {
    "NEW": ("UNDER_REVIEW", "ACCEPTED", "REJECTED", "DEFERRED"),
    "UNDER_REVIEW": ("ACCEPTED", "REJECTED", "MERGED", "DEFERRED"),
    "ACCEPTED": (),
    "REJECTED": (),
    "MERGED": (),
    "DEFERRED": ("UNDER_REVIEW",),  # operators can re-open
}


def is_legal_version_transition(*, from_state: str, to_state: str) -> bool:
    """Return True when ``from_state → to_state`` is a legal version transition.

    Mirrors :data:`_ALLOWED_VERSION_TRANSITIONS`; exposed at module level
    so the store / route layers can short-circuit illegal requests
    before any database write happens.
    """
    return to_state in _ALLOWED_VERSION_TRANSITIONS.get(from_state, ())


def is_legal_concept_transition(*, from_state: str, to_state: str) -> bool:
    """Return True when ``from_state → to_state`` is a legal concept transition."""
    return to_state in _ALLOWED_CONCEPT_TRANSITIONS.get(from_state, ())


# ─── ConceptSuggestion ─────────────────────────────────────────────────


class ConceptSuggestion(BaseModel):
    """One proposed concept attached to a DRAFT :class:`TaxonomyVersion`.

    The concept's content (label + description + parent) is what the
    suggestion *proposes* to add to the tree on promotion. The
    :attr:`state` tracks where the suggestion sits in the review
    workflow; :attr:`source` records which subsystem produced it.

    ``merge_target_id`` is set only when :attr:`state` is ``MERGED`` —
    it points at the existing category the suggestion was folded into
    so the audit trail can replay the merge later.
    """

    schema_version: Literal["v0.1"] = TAXONOMY_VERSION_SCHEMA_VERSION
    suggestion_id: str = Field(default_factory=lambda: uuid4().hex)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = Field(
        default=None,
        description=(
            "When set, the suggestion proposes a subcategory under "
            "this existing category id. ``None`` means a new "
            "top-level category."
        ),
    )
    source: ConceptSuggestionSource = "extractor"
    state: ConceptSuggestionState = "NEW"
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    merge_target_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state_changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None
    last_actor: str | None = None

    @model_validator(mode="after")
    def _merge_target_only_when_merged(self) -> ConceptSuggestion:
        if self.merge_target_id is not None and self.state != "MERGED":
            raise ValueError(
                "merge_target_id must only be set when state == 'MERGED'."
            )
        if self.state == "MERGED" and self.merge_target_id is None:
            raise ValueError(
                "state == 'MERGED' requires merge_target_id to be set."
            )
        return self


# ─── TaxonomyVersion ───────────────────────────────────────────────────


class TaxonomyVersion(BaseModel):
    """One versioned taxonomy resource (ADR-018 §9).

    Wraps the existing :class:`Taxonomy` tree with the lifecycle
    metadata: stable id, monotonic version number, state, audit
    timestamps, optional concept-suggestion list (populated only in
    DRAFT versions).

    Identity is ``(taxonomy_id, version_number)``. The integer is the
    audit-trail key; :attr:`version_label` is a free-text display
    field operators set on promotion.
    """

    schema_version: Literal["v0.1"] = TAXONOMY_VERSION_SCHEMA_VERSION
    taxonomy_id: str = Field(default_factory=lambda: uuid4().hex)
    version_number: int = Field(ge=1)
    version_label: str | None = Field(default=None, max_length=200)
    state: TaxonomyState
    taxonomy: Taxonomy = Field(default_factory=Taxonomy)
    suggestions: list[ConceptSuggestion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state_changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None
    superseded_version_number: int | None = Field(
        default=None,
        description=(
            "When :attr:`state` is ``ARCHIVED`` or this is a "
            "promoted ``VALIDATED_V1``, points at the previous "
            "Validated version this one supersedes. ``None`` for the "
            "first Validated and for Drafts / Candidates."
        ),
    )

    @model_validator(mode="after")
    def _suggestions_only_on_drafts(self) -> TaxonomyVersion:
        if self.suggestions and self.state != "DRAFT":
            raise ValueError(
                "Concept suggestions only live on DRAFT versions; "
                f"got state={self.state!r}. Promote the draft to land "
                "accepted / merged suggestions into the taxonomy tree."
            )
        return self


__all__ = [
    "ConceptSuggestion",
    "ConceptSuggestionSource",
    "ConceptSuggestionState",
    "TAXONOMY_VERSION_SCHEMA_VERSION",
    "TaxonomyState",
    "TaxonomyVersion",
    "is_legal_concept_transition",
    "is_legal_version_transition",
]
