from enum import StrEnum


class DocumentVersionStatus(StrEnum):
    UPLOADED = "UPLOADED"
    HASHED = "HASHED"
    DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
    STORED = "STORED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    SEMANTIC_READY = "SEMANTIC_READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


# Lifecycle FSM for a DocumentVersion. Maps each state to the set of states
# it is allowed to transition to. Terminal states (DUPLICATE_DETECTED,
# VALIDATED, REJECTED) map to the empty set — once reached, a version is
# frozen.
#
# ``FAILED`` is *not* fully terminal: it allows a single ``FAILED →
# EXTRACTING`` edge so a reviewer can retry extraction after the
# underlying issue (missing parser, transient infra error, bad config)
# is fixed (#87). Validated and rejected versions stay frozen — retry
# never bypasses the review gate.
#
# ``UPLOADED`` and ``HASHED`` are defensive entries: not every flow walks
# through them today, but if a future ingestion path does, the only
# sane next steps are STORED or FAILED.
ALLOWED_TRANSITIONS: dict[DocumentVersionStatus, frozenset[DocumentVersionStatus]] = {
    DocumentVersionStatus.UPLOADED: frozenset(
        {DocumentVersionStatus.STORED, DocumentVersionStatus.FAILED}
    ),
    DocumentVersionStatus.HASHED: frozenset(
        {DocumentVersionStatus.STORED, DocumentVersionStatus.FAILED}
    ),
    DocumentVersionStatus.STORED: frozenset(
        {DocumentVersionStatus.EXTRACTING, DocumentVersionStatus.FAILED}
    ),
    DocumentVersionStatus.EXTRACTING: frozenset(
        {DocumentVersionStatus.EXTRACTED, DocumentVersionStatus.FAILED}
    ),
    DocumentVersionStatus.EXTRACTED: frozenset(
        {
            DocumentVersionStatus.SEMANTIC_READY,
            DocumentVersionStatus.NEEDS_REVIEW,
            DocumentVersionStatus.FAILED,
        }
    ),
    DocumentVersionStatus.SEMANTIC_READY: frozenset(
        {DocumentVersionStatus.NEEDS_REVIEW, DocumentVersionStatus.FAILED}
    ),
    DocumentVersionStatus.NEEDS_REVIEW: frozenset(
        {DocumentVersionStatus.VALIDATED, DocumentVersionStatus.REJECTED}
    ),
    DocumentVersionStatus.DUPLICATE_DETECTED: frozenset(),
    DocumentVersionStatus.VALIDATED: frozenset(),
    DocumentVersionStatus.REJECTED: frozenset(),
    DocumentVersionStatus.FAILED: frozenset({DocumentVersionStatus.EXTRACTING}),
}


def _build_allowed_predecessors() -> dict[DocumentVersionStatus, frozenset[DocumentVersionStatus]]:
    """Reverse ``ALLOWED_TRANSITIONS`` so each target maps to the set of states
    it's reachable from. Used by ``update_version_status`` to constrain the
    SQL ``UPDATE`` predicate, so a concurrent writer can't slip a second
    transition through after the FSM check has run but before the row is
    written. Every status is present, even if unreachable, so callers don't
    have to guard ``KeyError``."""
    predecessors: dict[DocumentVersionStatus, set[DocumentVersionStatus]] = {
        status: set() for status in DocumentVersionStatus
    }
    for current, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            predecessors[target].add(current)
    return {status: frozenset(states) for status, states in predecessors.items()}


# For each *target* status, the set of states a version must currently be in
# for the transition to be legal. Derived once at import time from the FSM
# above; see ``_build_allowed_predecessors`` for why it exists.
ALLOWED_PREDECESSORS: dict[DocumentVersionStatus, frozenset[DocumentVersionStatus]] = (
    _build_allowed_predecessors()
)


class IllegalTransition(ValueError):
    """Raised when a status transition is forbidden by the FSM, OR when a
    concurrent writer changed the row out from under us (the row's current
    status is no longer in the expected predecessor set).

    Subclasses ``ValueError`` so existing route handlers that translate
    ``ValueError -> 409`` keep behaving as before; callers that want to
    distinguish FSM violations from other ``ValueError``s can catch this
    type explicitly.
    """


def assert_transition(current: DocumentVersionStatus, target: DocumentVersionStatus) -> None:
    """Raise ``IllegalTransition`` if ``current -> target`` is not in the
    allowed FSM.

    The error message includes both state values so HTTP routes can surface a
    self-explanatory 409 detail without re-deriving context from the caller.
    """
    if target not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"Cannot transition from {current.value} to {target.value}")
