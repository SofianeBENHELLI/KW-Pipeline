"""Tests for the deterministic SOP detector + parser (#390).

Covers:

* :func:`detect_sop_structure` — conservative on non-procedural
  docs, triggers on each of the three procedural patterns
  (``## Step N`` headings, numbered lists, ``Step N:`` lines).
* :func:`extract_process` — emits a :class:`Process` for procedural
  docs with ordered :class:`ProcessStep` rows whose
  ``source_reference_ids`` trace back to the section ids; returns
  ``None`` for non-procedural input.
* Projector hook — fires the parser after a successful projection,
  short-circuits on non-procedural docs, replaces the prior
  Process atomically on re-projection, and swallows store
  failures (fire-and-log per ADR-012 §3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.process import Process
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.projector import KnowledgeProjector
from app.services.process_store import InMemoryProcessStore, ProcessStore
from app.services.sop_extractor import detect_sop_structure, extract_process

# ─── Fixtures ─────────────────────────────────────────────────────


def _make_version(version_id: str = "ver-1") -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id="doc-sop",
        version_number=1,
        filename="sop.md",
        content_type="text/markdown",
        file_size=42,
        sha256="0" * 64,
        storage_uri="memory://sop",
        status=DocumentVersionStatus.VALIDATED,
    )


def _make_document(version: DocumentVersion) -> Document:
    return Document(
        id=version.document_id,
        original_filename=version.filename,
        latest_version_id=version.id,
        versions=[version],
    )


def _make_semantic(
    *,
    version: DocumentVersion,
    sections: list[SemanticSection],
    title: str = "Onboarding SOP",
) -> SemanticDocument:
    return SemanticDocument(
        id=f"sem-{version.id}",
        document_version_id=version.id,
        document_profile=DocumentProfile(title=title),
        sections=sections,
        validation_status="validated",
        markdown="# x\n",
        created_at=datetime(2026, 5, 11, tzinfo=UTC),
    )


# ─── detect_sop_structure: returns False on ──────────────────────


def test_detect_returns_false_on_empty_semantic() -> None:
    version = _make_version()
    semantic = _make_semantic(version=version, sections=[])
    assert detect_sop_structure(semantic) is False


def test_detect_returns_false_on_non_procedural_policy_doc() -> None:
    """A single section of prose without numbered structure must not
    be flagged. The downstream consumer (AURA companion) reads
    procedural docs as Processes; flagging a policy document would
    poison the procedural surface."""
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Travel Policy",
                text=(
                    "Employees may book flights through the corporate portal. "
                    "Reimbursements require receipts uploaded within 30 days. "
                    "Premium economy is permitted for flights over six hours."
                ),
            )
        ],
    )
    assert detect_sop_structure(semantic) is False


def test_detect_returns_false_on_two_numbered_items_below_threshold() -> None:
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Setup",
                text=(
                    "1. Open the dashboard and authenticate with your SSO.\n"
                    "2. Click the New Project button to begin onboarding.\n"
                ),
            )
        ],
    )
    assert detect_sop_structure(semantic) is False


def test_detect_returns_false_on_table_of_contents_only() -> None:
    """TOC entries are short labels with no procedural body — the
    body-content guard catches them. This is the canonical false-
    positive guard the brief calls out explicitly."""
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Contents",
                text=("1. Background\n2. Scope\n3. References\n4. Glossary\n"),
            )
        ],
    )
    assert detect_sop_structure(semantic) is False


# ─── detect_sop_structure: returns True on ───────────────────────


def test_detect_returns_true_on_three_step_markdown_headings() -> None:
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="## Step 1",
                text="Open the control panel and sign in.",
            ),
            SemanticSection(
                id="s-2",
                heading="## Step 2",
                text="Select the new-hire workflow.",
            ),
            SemanticSection(
                id="s-3",
                heading="### step 3",
                text="Confirm the assignment and click Submit.",
            ),
        ],
    )
    assert detect_sop_structure(semantic) is True


def test_detect_returns_true_on_three_numbered_items_in_one_section() -> None:
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Onboarding steps",
                text=(
                    "1. Open the IT portal and request the laptop image.\n"
                    "2. Install the security agent before the first sign-in.\n"
                    "3. Pair with the buddy reviewer to walk the codebase.\n"
                    "4. Submit the day-1 self-assessment form before EOD.\n"
                ),
            )
        ],
    )
    assert detect_sop_structure(semantic) is True


def test_detect_returns_true_on_three_step_n_lines() -> None:
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Procedure",
                text=(
                    "Step 1: Power down the unit and disconnect the supply.\n"
                    "Step 2: Remove the access panel using the T15 driver.\n"
                    "Step 3: Replace the filter cartridge and reseal.\n"
                ),
            )
        ],
    )
    assert detect_sop_structure(semantic) is True


# ─── extract_process: shape of the emitted Process ───────────────


def test_extract_process_returns_none_for_non_procedural_doc() -> None:
    version = _make_version()
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Policy",
                text="Single paragraph of policy prose, no structure.",
            )
        ],
    )
    assert extract_process(semantic, document=document, version=version) is None


def test_extract_process_links_document_and_version_ids() -> None:
    version = _make_version("ver-42")
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Steps",
                text=(
                    "1. Verify the input is well-formed and reject otherwise.\n"
                    "2. Acquire the per-tenant lock before mutation.\n"
                    "3. Apply the patch and emit the audit log entry.\n"
                ),
            )
        ],
    )
    process = extract_process(semantic, document=document, version=version)
    assert process is not None
    assert process.document_id == document.id
    assert process.version_id == version.id
    # ID is deterministic per the brief — ``process-{version.id}``.
    assert process.id == f"process-{version.id}"


def test_extract_process_steps_are_ordered_starting_at_one() -> None:
    version = _make_version()
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="## Step 1",
                text="Open the panel and sign in to the console.",
            ),
            SemanticSection(
                id="s-2",
                heading="## Step 2",
                text="Select the workflow and confirm.",
            ),
            SemanticSection(
                id="s-3",
                heading="## Step 3",
                text="Submit and verify the status change.",
            ),
        ],
    )
    process = extract_process(semantic, document=document, version=version)
    assert process is not None
    assert [step.step_number for step in process.steps] == [1, 2, 3]


def test_extract_process_steps_carry_section_id_in_source_references() -> None:
    """Each step's ``source_reference_ids`` must include the section
    id it was derived from so AURA citation surfaces (ADR-029) can
    trace back to the source chunk."""
    version = _make_version()
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="section-alpha",
                heading="## Step 1",
                text="Authenticate against the gateway with the service token.",
            ),
            SemanticSection(
                id="section-beta",
                heading="## Step 2",
                text="Issue the migration request and capture the job id.",
            ),
            SemanticSection(
                id="section-gamma",
                heading="## Step 3",
                text="Poll the job until it reports completed.",
            ),
        ],
    )
    process = extract_process(semantic, document=document, version=version)
    assert process is not None
    section_ids = [step.source_reference_ids for step in process.steps]
    assert section_ids == [
        ["section-alpha"],
        ["section-beta"],
        ["section-gamma"],
    ]


def test_extract_process_steps_have_empty_preconditions_and_outcomes() -> None:
    """The deterministic parser leaves these for the future LLM
    pass — they default to empty lists, not None."""
    version = _make_version()
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Procedure",
                text=(
                    "1. Power down the unit and disconnect the supply lead.\n"
                    "2. Remove the access panel using the T15 driver.\n"
                    "3. Replace the filter cartridge and reseal the panel.\n"
                ),
            )
        ],
    )
    process = extract_process(semantic, document=document, version=version)
    assert process is not None
    for step in process.steps:
        assert step.preconditions == []
        assert step.outcomes == []


# ─── Projector hook integration ──────────────────────────────────


def _sop_semantic(version: DocumentVersion) -> SemanticDocument:
    return _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="## Step 1",
                text="Open the IT portal and request laptop provisioning.",
            ),
            SemanticSection(
                id="s-2",
                heading="## Step 2",
                text="Install the security agent before first sign-in.",
            ),
            SemanticSection(
                id="s-3",
                heading="## Step 3",
                text="Confirm the buddy reviewer pairing in the wiki.",
            ),
        ],
    )


def _flat_semantic(version: DocumentVersion) -> SemanticDocument:
    return _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s-1",
                heading="Reference",
                text="Single paragraph of reference material with no structure.",
            )
        ],
    )


def test_projector_writes_process_for_sop_when_store_wired() -> None:
    store = InMemoryProcessStore()
    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_process_store(cast(ProcessStore, store))

    version = _make_version()
    document = _make_document(version)
    semantic = _sop_semantic(version)

    projector.project(document=document, version=version, semantic=semantic)

    process = store.get(f"process-{version.id}")
    assert process is not None
    assert process.version_id == version.id
    assert len(process.steps) == 3


def test_projector_without_process_store_does_not_emit_process() -> None:
    """Regression guard: non-procedural docs continue to project
    without anyone being surprised by a Process row appearing."""
    store = InMemoryProcessStore()
    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    # No set_process_store call — pre-#390 behaviour.

    version = _make_version()
    document = _make_document(version)
    semantic = _flat_semantic(version)

    projector.project(document=document, version=version, semantic=semantic)

    # The store wasn't wired, so nothing was written — and nobody
    # else has a reference to write through it either.
    summaries, _cursor = store.list()
    assert summaries == []


def test_projector_with_store_skips_non_procedural_doc() -> None:
    """The detector short-circuits inside ``extract_process``; the
    store stays empty even though it's wired."""
    store = InMemoryProcessStore()
    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_process_store(cast(ProcessStore, store))

    version = _make_version()
    document = _make_document(version)
    semantic = _flat_semantic(version)

    projector.project(document=document, version=version, semantic=semantic)

    summaries, _cursor = store.list()
    assert summaries == []


def test_projector_replaces_prior_process_on_re_projection() -> None:
    """Re-projecting the same SOP version must yield exactly one
    Process row — the prior one is dropped via
    ``delete_for_version`` before the new one is saved."""
    store = InMemoryProcessStore()
    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_process_store(cast(ProcessStore, store))

    version = _make_version()
    document = _make_document(version)
    semantic = _sop_semantic(version)

    projector.project(document=document, version=version, semantic=semantic)
    projector.project(document=document, version=version, semantic=semantic)

    summaries, _cursor = store.list()
    assert len(summaries) == 1
    assert summaries[0].version_id == version.id


def test_projector_swallows_save_failures() -> None:
    """A store hiccup during save must not bubble up — the
    structural projection already wrote successfully and the SOP
    parser is a fire-and-log boundary per ADR-012 §3."""

    class _ExplodingStore:
        def save_process(self, process: Process) -> None:
            raise RuntimeError("simulated save_process failure")

        def get(self, process_id: str):  # type: ignore[no-untyped-def]
            return None

        def list(self, *, cursor=None, limit=50):  # type: ignore[no-untyped-def]
            return ([], None)

        def delete_for_version(self, version_id: str) -> int:
            return 0

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_process_store(cast(ProcessStore, _ExplodingStore()))

    version = _make_version()
    document = _make_document(version)
    semantic = _sop_semantic(version)

    # Should not raise — the boundary catches it and logs a warning.
    projector.project(document=document, version=version, semantic=semantic)


# ─── Wiring smoke test ──────────────────────────────────────────


def test_dependencies_wire_process_store_into_projector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_services`` must wire the process_store onto the
    projector so the SOP parser fires after every projection. The
    knowledge layer is gated by ``KW_KNOWLEDGE_LAYER_ENABLED``;
    enable it explicitly so the projector exists to wire."""
    from app.dependencies import build_services

    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    services = build_services()
    projector = services.knowledge_projector
    assert projector is not None
    # Private attribute used to assert the wire — the public surface
    # is the setter we added in this slice.
    assert projector._process_store is services.process_store  # type: ignore[attr-defined]
