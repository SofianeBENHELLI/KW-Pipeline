"""Lifecycle FSM enforcement on DocumentService.update_status.

Covers:

* the ``ALLOWED_TRANSITIONS`` map and the ``assert_transition`` helper at
  the model layer (every legal transition is accepted; one illegal example
  raises ``ValueError`` with both states named in the message);
* every legal transition reachable through ``DocumentService.update_status``
  (parametrized);
* a couple of representative illegal transitions through the service layer
  (the same ``ValueError`` shape, with the catalog left untouched);
* an HTTP-level test that an illegal transition reaching the extract route
  surfaces as a 409 with both states in the detail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import (
    ALLOWED_PREDECESSORS,
    ALLOWED_TRANSITIONS,
    DocumentVersionStatus,
    IllegalTransition,
    assert_transition,
)
from app.schemas.document import Document, DocumentVersion
from app.services.document_service import DocumentService
from app.services.storage_service import InMemoryStorageService

# All FSM edges are listed explicitly so a regression in
# ALLOWED_TRANSITIONS is caught here, not three layers up. When this list and
# the map disagree the table-test below fails — that's intentional.
LEGAL_TRANSITIONS: list[tuple[DocumentVersionStatus, DocumentVersionStatus]] = [
    (DocumentVersionStatus.UPLOADED, DocumentVersionStatus.STORED),
    (DocumentVersionStatus.UPLOADED, DocumentVersionStatus.FAILED),
    (DocumentVersionStatus.HASHED, DocumentVersionStatus.STORED),
    (DocumentVersionStatus.HASHED, DocumentVersionStatus.FAILED),
    (DocumentVersionStatus.STORED, DocumentVersionStatus.EXTRACTING),
    (DocumentVersionStatus.STORED, DocumentVersionStatus.FAILED),
    (DocumentVersionStatus.EXTRACTING, DocumentVersionStatus.EXTRACTED),
    (DocumentVersionStatus.EXTRACTING, DocumentVersionStatus.FAILED),
    (DocumentVersionStatus.EXTRACTED, DocumentVersionStatus.SEMANTIC_READY),
    (DocumentVersionStatus.EXTRACTED, DocumentVersionStatus.NEEDS_REVIEW),
    (DocumentVersionStatus.EXTRACTED, DocumentVersionStatus.FAILED),
    (DocumentVersionStatus.SEMANTIC_READY, DocumentVersionStatus.NEEDS_REVIEW),
    (DocumentVersionStatus.SEMANTIC_READY, DocumentVersionStatus.FAILED),
    (DocumentVersionStatus.NEEDS_REVIEW, DocumentVersionStatus.VALIDATED),
    (DocumentVersionStatus.NEEDS_REVIEW, DocumentVersionStatus.REJECTED),
    # The retry-extraction edge introduced by #87.
    (DocumentVersionStatus.FAILED, DocumentVersionStatus.EXTRACTING),
    # ADR-025: VALIDATED → SUPERSEDED, fired automatically by
    # ``ReviewService.handle_validation`` when a newer sibling
    # validates. ``SUPERSEDED`` itself is fully terminal w.r.t. the
    # review/lifecycle FSM (the ADR-027 PURGED edge below is admin-
    # only and never fires from review code).
    (DocumentVersionStatus.VALIDATED, DocumentVersionStatus.SUPERSEDED),
    # ADR-027 §3 / slice 6: every previously-terminal status can
    # transition to PURGED via the ``purge_artifacts`` admin route.
    # The route never calls ``update_status`` directly (it goes through
    # ``CatalogStore.purge_version_artifacts``), but the FSM still has
    # to admit the edge for ``assert_transition`` to accept it on
    # parity test paths.
    (DocumentVersionStatus.VALIDATED, DocumentVersionStatus.PURGED),
    (DocumentVersionStatus.REJECTED, DocumentVersionStatus.PURGED),
    (DocumentVersionStatus.SUPERSEDED, DocumentVersionStatus.PURGED),
    (DocumentVersionStatus.FAILED, DocumentVersionStatus.PURGED),
    (DocumentVersionStatus.DUPLICATE_DETECTED, DocumentVersionStatus.PURGED),
    # PURGED → PURGED self-loop so the catalog idempotent re-purge
    # path stays valid against the FSM (the route layer short-circuits
    # before calling update_status, but a future caller that goes
    # through the FSM check shouldn't surprise-raise).
    (DocumentVersionStatus.PURGED, DocumentVersionStatus.PURGED),
]

# Truly-terminal states — once reached, no outgoing transitions exist
# **except** the admin-only ``* → PURGED`` edge from ADR-027 §3.
# ``FAILED`` is intentionally NOT here: issue #87 added a controlled
# ``FAILED → EXTRACTING`` edge for the retry-extraction surface (and
# ADR-027 added the FAILED → PURGED edge). The transition is exercised
# separately. ``VALIDATED`` is also intentionally NOT here: ADR-025 +
# ADR-027 give it three outgoing edges (SUPERSEDED, PURGED, NEEDS_REVIEW
# — the demote escape valve added in #435). ``REJECTED`` was removed
# from the terminal list when #435 added the REJECTED → NEEDS_REVIEW
# demote edge — it's no longer a true terminal. ``DUPLICATE_DETECTED``
# and ``SUPERSEDED`` keep only the PURGED edge.
TERMINAL_STATES: list[DocumentVersionStatus] = [
    DocumentVersionStatus.DUPLICATE_DETECTED,
    DocumentVersionStatus.SUPERSEDED,
]


def _make_service_with_version(
    status: DocumentVersionStatus,
) -> tuple[DocumentService, str, str]:
    """Build an isolated DocumentService and seed one version directly into the
    in-memory catalog at ``status``. Bypassing ``upload`` lets us start from any
    arbitrary state — including states the upload path never produces."""
    service = DocumentService(storage=InMemoryStorageService())
    version = DocumentVersion(
        id="ver-1",
        document_id="doc-1",
        version_number=1,
        filename="seed.txt",
        content_type="text/plain",
        file_size=4,
        sha256="a" * 64,
        storage_uri="memory://documents/ver-1/seed.txt",
        status=status,
    )
    document = Document.with_first_version(version)
    service.catalog.save_document_with_version(document=document, version=version)
    return service, version.document_id, version.id


class TestAllowedTransitionsMap:
    def test_every_status_appears_in_allowed_transitions(self):
        """A status missing from the map would let the FSM check raise
        ``KeyError`` instead of ``ValueError`` — guard against that."""
        for status in DocumentVersionStatus:
            assert status in ALLOWED_TRANSITIONS

    @pytest.mark.parametrize("terminal", TERMINAL_STATES)
    def test_terminal_states_only_point_at_purged(self, terminal: DocumentVersionStatus):
        # ADR-027 §3 / slice 6: every previously-terminal status now
        # has exactly one outgoing edge — to PURGED. Fired only by
        # the ``purge_artifacts`` admin route via
        # ``CatalogStore.purge_version_artifacts``; no other code
        # path may write PURGED.
        assert ALLOWED_TRANSITIONS[terminal] == frozenset({DocumentVersionStatus.PURGED})


class TestAllowedPredecessorsMap:
    def test_every_status_appears(self):
        """Every status — including those with no incoming edges — has an
        entry, so callers never have to guard ``KeyError``."""
        for status in DocumentVersionStatus:
            assert status in ALLOWED_PREDECESSORS

    def test_predecessor_map_is_reverse_of_transitions(self):
        """For every legal ``current -> target`` edge, ``current`` must be in
        ``ALLOWED_PREDECESSORS[target]`` and nothing else may be."""
        expected: dict[DocumentVersionStatus, set[DocumentVersionStatus]] = {
            status: set() for status in DocumentVersionStatus
        }
        for current, targets in ALLOWED_TRANSITIONS.items():
            for target in targets:
                expected[target].add(current)
        for target, predecessors in expected.items():
            assert ALLOWED_PREDECESSORS[target] == frozenset(predecessors)

    def test_initial_states_have_no_predecessors(self):
        """UPLOADED and HASHED are entry points: nothing transitions *to* them."""
        assert ALLOWED_PREDECESSORS[DocumentVersionStatus.UPLOADED] == frozenset()
        assert ALLOWED_PREDECESSORS[DocumentVersionStatus.HASHED] == frozenset()
        # DUPLICATE_DETECTED is set on creation, not via update_status.
        assert ALLOWED_PREDECESSORS[DocumentVersionStatus.DUPLICATE_DETECTED] == frozenset()


class TestAssertTransition:
    @pytest.mark.parametrize(("current", "target"), LEGAL_TRANSITIONS)
    def test_legal_transition_does_not_raise(
        self, current: DocumentVersionStatus, target: DocumentVersionStatus
    ):
        assert_transition(current, target)

    def test_illegal_transition_raises_with_both_states(self):
        with pytest.raises(IllegalTransition) as excinfo:
            assert_transition(DocumentVersionStatus.STORED, DocumentVersionStatus.VALIDATED)

        message = str(excinfo.value)
        assert "STORED" in message
        assert "VALIDATED" in message
        assert "Cannot transition" in message

    def test_illegal_transition_is_a_value_error(self):
        """``IllegalTransition`` subclasses ``ValueError`` so existing routes
        that translate ``ValueError -> 409`` keep working unchanged."""
        with pytest.raises(ValueError):
            assert_transition(DocumentVersionStatus.STORED, DocumentVersionStatus.VALIDATED)

    @pytest.mark.parametrize("terminal", TERMINAL_STATES)
    def test_no_transitions_out_of_terminal_states_except_purged(
        self, terminal: DocumentVersionStatus
    ):
        # Pick any status other than the terminal itself AND PURGED
        # — those are the only two legal moves (terminal → terminal
        # is not legal except for PURGED → PURGED, which is the
        # idempotent re-purge case captured in TERMINAL_STATES'
        # docstring above).
        target = next(
            s
            for s in DocumentVersionStatus
            if s != terminal and s is not DocumentVersionStatus.PURGED
        )
        with pytest.raises(IllegalTransition, match="Cannot transition"):
            assert_transition(terminal, target)


class TestDocumentServiceUpdateStatusFSM:
    @pytest.mark.parametrize(("current", "target"), LEGAL_TRANSITIONS)
    def test_legal_transitions_through_service_succeed(
        self, current: DocumentVersionStatus, target: DocumentVersionStatus
    ):
        service, document_id, version_id = _make_service_with_version(current)

        updated = service.update_status(document_id, version_id, target)

        assert updated.status == target
        assert service.get_version(document_id, version_id).status == target

    def test_illegal_transition_raises_value_error_with_both_states(self):
        service, document_id, version_id = _make_service_with_version(DocumentVersionStatus.STORED)

        with pytest.raises(IllegalTransition) as excinfo:
            service.update_status(document_id, version_id, DocumentVersionStatus.VALIDATED)

        message = str(excinfo.value)
        assert "STORED" in message
        assert "VALIDATED" in message

    def test_illegal_transition_does_not_mutate_catalog(self):
        """The guard runs before the catalog write, so a refused transition
        leaves the version in its original state."""
        service, document_id, version_id = _make_service_with_version(DocumentVersionStatus.STORED)

        with pytest.raises(IllegalTransition):
            service.update_status(document_id, version_id, DocumentVersionStatus.NEEDS_REVIEW)

        assert service.get_version(document_id, version_id).status == DocumentVersionStatus.STORED

    def test_terminal_state_refuses_self_transition(self):
        """A version that already FAILED cannot be re-marked FAILED via
        ``update_status`` — terminal-out edges are empty in the FSM."""
        service, document_id, version_id = _make_service_with_version(DocumentVersionStatus.FAILED)

        with pytest.raises(IllegalTransition, match="Cannot transition from FAILED"):
            service.update_status(document_id, version_id, DocumentVersionStatus.FAILED)


class TestUpdateStatusHTTPFlow:
    """Routes that surface ``ValueError`` from the service must respond 409."""

    def test_extract_after_validated_returns_409(self):
        """Drive a version all the way to VALIDATED via the public API, then
        ask /extract to fire a STORED-only transition. The FSM refuses, the
        route translates the ``ValueError`` to 409, and the detail contains
        both state names so the caller can debug without inspecting logs."""
        services = build_services()
        client = TestClient(create_app(services=services))

        version = client.post(
            "/documents/upload",
            files={"file": ("policy.txt", b"some text", "text/plain")},
        ).json()
        document_id, version_id = version["document_id"], version["id"]

        # Walk to NEEDS_REVIEW through legal transitions, then validate.
        client.post(f"/documents/{document_id}/versions/{version_id}/extract")
        client.post(f"/documents/{document_id}/versions/{version_id}/semantic")
        client.post(f"/documents/{document_id}/versions/{version_id}/validate", json={})

        response = client.post(f"/documents/{document_id}/versions/{version_id}/extract")

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "VALIDATED" in detail
        assert "EXTRACTING" in detail
