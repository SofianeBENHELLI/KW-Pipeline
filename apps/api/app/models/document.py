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
    # ADR-025: terminal status assigned to a previously-validated version
    # when a newer version of the same document family is validated.
    # ``SUPERSEDED`` is filtered out by catalog/search/chat consumers but
    # remains visible to the audit/Orbital surfaces so the version
    # history is preserved.
    SUPERSEDED = "SUPERSEDED"
    # ADR-027 §3: terminal status assigned to every version in a
    # document family by the ``purge_artifacts`` admin route. The
    # version's ``storage_uri`` is overwritten with a tombstone marker
    # (``tombstone:purged:<doc>:<version>:<iso>``) and the bytes /
    # extractions / semantic JSON / Markdown asset are physically
    # deleted via ``StorageService.delete``. The catalog row stays put
    # — read paths surface ``PURGED`` as HTTP 410 Gone instead of
    # 404 so consumers can distinguish "never existed" from "purged".
    # Reachable only from a previously **terminal** status
    # (``VALIDATED`` / ``REJECTED`` / ``FAILED`` / ``SUPERSEDED`` /
    # ``DUPLICATE_DETECTED`` / ``PURGED``) because ``purge_artifacts``
    # requires the document to be archived first, and the orphan
    # cascade only flag-archives families whose versions have all
    # reached a terminal state.
    PURGED = "PURGED"


# Lifecycle FSM for a DocumentVersion. Maps each state to the set of states
# it is allowed to transition to. Terminal states (DUPLICATE_DETECTED,
# REJECTED, SUPERSEDED) map to the empty set — once reached, a version is
# frozen.
#
# ``VALIDATED`` is mostly terminal but has a single outgoing edge to
# ``SUPERSEDED`` — fired by ``ReviewService.handle_validation`` when a
# newer sibling version is validated (ADR-025). No other path may
# transition out of ``VALIDATED``.
#
# ``FAILED`` is *not* fully terminal: it allows a single ``FAILED →
# EXTRACTING`` edge so a reviewer can retry extraction after the
# underlying issue (missing parser, transient infra error, bad config)
# is fixed (#87). Validated, rejected, and superseded versions stay
# frozen — retry never bypasses the review gate.
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
    # ADR-027 §3: every previously-terminal status can transition to
    # PURGED, fired exclusively by the ``purge_artifacts`` admin route.
    # Intermediate states (UPLOADED/HASHED/STORED/EXTRACTING/EXTRACTED/
    # SEMANTIC_READY/NEEDS_REVIEW) cannot reach PURGED because the
    # archive precondition (``documents.archived_at IS NOT NULL``)
    # implies every version in the family has already settled into a
    # terminal state — the orphan cascade flag-archives families whose
    # versions are no longer in motion.
    DocumentVersionStatus.DUPLICATE_DETECTED: frozenset({DocumentVersionStatus.PURGED}),
    # ADR-025: VALIDATED → SUPERSEDED is the only legal exit edge,
    # plus ADR-027 § 3's terminal → PURGED transition.
    DocumentVersionStatus.VALIDATED: frozenset(
        {DocumentVersionStatus.SUPERSEDED, DocumentVersionStatus.PURGED}
    ),
    DocumentVersionStatus.REJECTED: frozenset({DocumentVersionStatus.PURGED}),
    DocumentVersionStatus.SUPERSEDED: frozenset({DocumentVersionStatus.PURGED}),
    DocumentVersionStatus.FAILED: frozenset(
        {DocumentVersionStatus.EXTRACTING, DocumentVersionStatus.PURGED}
    ),
    # PURGED → PURGED is admitted as a no-op so the catalog method can
    # treat re-purge as idempotent (returns the existing tombstone
    # without re-emitting an audit row). The route layer never actually
    # fires the FSM check on an already-PURGED row — it short-circuits
    # to the existing tombstone — but keeping the self-loop in the FSM
    # avoids surprising IllegalTransition raises if a future caller
    # naively retries via ``update_version_status``.
    DocumentVersionStatus.PURGED: frozenset({DocumentVersionStatus.PURGED}),
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
