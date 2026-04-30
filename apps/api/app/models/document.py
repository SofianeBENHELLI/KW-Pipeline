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
# VALIDATED, REJECTED, FAILED) map to the empty set — once reached, a version
# is frozen. UPLOADED and HASHED are defensive entries: not every flow walks
# through them today, but if a future ingestion path does, the only sane next
# steps are STORED or FAILED.
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
    DocumentVersionStatus.FAILED: frozenset(),
}


def assert_transition(current: DocumentVersionStatus, target: DocumentVersionStatus) -> None:
    """Raise ``ValueError`` if ``current -> target`` is not in the allowed FSM.

    The error message includes both state values so HTTP routes can surface a
    self-explanatory 409 detail without re-deriving context from the caller.
    """
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Cannot transition from {current.value} to {target.value}")
