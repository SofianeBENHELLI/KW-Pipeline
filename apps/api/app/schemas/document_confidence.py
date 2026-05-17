"""Response schema for ``GET /documents/{document_id}/confidence`` —
the reviewer-UI confidence dashboard (converged plan §C.1).

Wraps the existing :class:`~app.schemas.validation_metadata.ConfidenceScore`
with the version context the dashboard needs to render: which version
is being scored, whether the score actually exists (versions that
predate the scorer or where ``KW_HITL_DISABLE_SCORER`` is set carry no
``ConfidenceScore``), the threshold the operator's deployment is
tuned to, and the lifecycle outcomes (routing decision + how it was
actually validated).

The dashboard does not own scoring — it's a strict read view over
:class:`ValidationMetadata` rows the scorer already produces on the
NEEDS_REVIEW transition (ADR-023). The route is the operator-facing
surface; the data has been there since EPIC-A slice 1.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.validation_metadata import (
    ConfidenceScore,
    RoutingMethod,
    ValidationMethod,
)

# Bumped when the wire shape of the response changes. v0.1 ships
# with the converged-plan §C.1 dashboard route.
DocumentConfidenceSchemaVersion = Literal["v0.1"]
DOCUMENT_CONFIDENCE_SCHEMA_VERSION: Final[DocumentConfidenceSchemaVersion] = "v0.1"


class DocumentConfidenceResponse(BaseModel):
    """Composite confidence view for one document's reported version.

    The route reports on the document's ``latest_version_id`` by
    default. Operators inspecting historical drift can target a
    specific version via the ``?version_id=`` query param; the
    response carries the resolved id so the frontend can confirm
    which version it's rendering.

    ``has_score`` is ``False`` when the resolved version exists but
    no :class:`~app.schemas.validation_metadata.ConfidenceScore` was
    persisted — either the scorer was disabled
    (``KW_HITL_DISABLE_SCORER`` truthy), the version never reached
    NEEDS_REVIEW under the scorer's wiring, or the version predates
    the scorer (legacy data). The frontend renders a "no confidence
    data" empty state for the score itself.

    ``routing_decision`` / ``validation_method`` / ``validation_actor``
    follow the underlying :class:`ValidationMetadata` row's presence
    independently of ``has_score``: when the scorer is disabled but
    the HITL router still ran (so the metadata row exists with a
    ``None`` score), the routing/validation outcome is still
    surfaced. Only when no metadata row exists at all are all three
    fields ``None``.

    ``auto_validate_threshold`` is included so the dashboard can
    render the pass / fail visual against the operator's configured
    cut-off (``KW_HITL_AUTO_VALIDATE_THRESHOLD``, default 0.85)
    without a second config round-trip. It's the threshold the HITL
    router uses for the ``auto`` routing decision; the score being
    above it does NOT mean the version was actually auto-validated
    (that's :attr:`validation_method`).
    """

    schema_version: DocumentConfidenceSchemaVersion = DOCUMENT_CONFIDENCE_SCHEMA_VERSION
    document_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    version_number: int = Field(ge=1)
    has_score: bool
    confidence_score: ConfidenceScore | None = None
    routing_decision: RoutingMethod | None = None
    validation_method: ValidationMethod | None = None
    validation_actor: str | None = Field(default=None, max_length=200)
    auto_validate_threshold: float = Field(ge=0.0, le=1.0)


__all__ = [
    "DOCUMENT_CONFIDENCE_SCHEMA_VERSION",
    "DocumentConfidenceResponse",
    "DocumentConfidenceSchemaVersion",
]
