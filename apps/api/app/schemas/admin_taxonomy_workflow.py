"""Admin route request / response schemas for the taxonomy versioning
workflow (EPIC-1 §1.8, ADR-018).

Wire shapes for ``POST /admin/taxonomy/*`` — the operator-facing
surface that drives the DRAFT → CANDIDATE_V0 → VALIDATED_V1 →
ARCHIVED lifecycle. The route layer reads these, calls the
transition functions in :mod:`app.services.taxonomy_version_store`,
and returns the resulting :class:`TaxonomyVersion`.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.taxonomy_version import (
    ConceptSuggestionState,
    TaxonomyState,
    TaxonomyVersion,
)


class CreateDraftRequest(BaseModel):
    """Body for ``POST /admin/taxonomy/drafts``.

    ``taxonomy_id`` + ``source_version_number`` together select the
    branching source. When both are omitted, a fresh taxonomy_id is
    minted and the draft starts empty. When ``taxonomy_id`` is set
    without ``source_version_number``, the draft is the next version
    for that taxonomy_id starting empty (rare but legal). When both
    are set, the draft inherits the source version's tree.
    """

    taxonomy_id: str | None = Field(default=None, max_length=200)
    source_version_number: int | None = Field(default=None, ge=1)


class TransitionVersionRequest(BaseModel):
    """Body for ``POST /admin/taxonomy/versions/{tid}/{vnum}/transition``.

    ``to_state`` selects the target lifecycle state — the route
    layer dispatches to the right transition function. ADR-018 §2
    pins the legal moves; an illegal move surfaces as 409 with the
    canonical ``IllegalTaxonomyTransition`` message.

    ``version_label`` only applies when transitioning to
    ``VALIDATED_V1``; ignored otherwise.

    ``reason`` only applies to ``ARCHIVED`` / ``DISCARDED``; lands
    on the structured-log audit event as the ``reason`` field.
    """

    to_state: TaxonomyState
    version_label: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)


class TransitionConceptRequest(BaseModel):
    """Body for ``POST /admin/taxonomy/versions/{tid}/{vnum}/concepts/{cid}/transition``.

    ``to_state`` selects the per-suggestion target state per
    ADR-018 §5. ``merge_target_id`` is **required** when transitioning
    to ``MERGED`` and rejected for every other target state
    (Pydantic validator enforces).

    ``reason`` lands on the audit event for accept / reject / merge /
    defer transitions.
    """

    to_state: ConceptSuggestionState
    merge_target_id: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)


class TaxonomyVersionListResponse(BaseModel):
    """Response for ``GET /admin/taxonomy/versions/{tid}``.

    Returns every version of one taxonomy_id sorted by version_number
    ascending. The Explorer / admin UI uses this to render the lineage
    panel (Draft 1 → Candidate V0 → Validated V1 → Archived …).
    """

    taxonomy_id: str
    versions: list[TaxonomyVersion] = Field(default_factory=list)


__all__ = [
    "CreateDraftRequest",
    "TaxonomyVersionListResponse",
    "TransitionConceptRequest",
    "TransitionVersionRequest",
]
