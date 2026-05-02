"""Knowledge-layer reconciliation (#124).

ADR-012 §4 commits to **fire-and-log** semantics for the post-validate
side-effects: a Neo4j outage or LLM hiccup must not roll back validation,
so the catalog is authoritative and the graph "catches up later". This
module is the "later" path — detection of versions that are VALIDATED in
the catalog but missing from the graph projection, and a one-shot repair
that re-runs projection (and entity extraction, when configured) for one
version.

The service is deliberately surface-agnostic: today it is consumed by
``apps/api/scripts/reconcile_knowledge_layer.py``, but a future admin
HTTP endpoint can wrap the same calls once auth lands (#83). Design
notes live in ``docs/runbook/reconciliation.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.semantic_document import SemanticDocument
from app.services.catalog_store import CatalogStore
from app.services.knowledge.entity_extractor import EntityExtractor
from app.services.knowledge.graph_store import GraphStore
from app.services.knowledge.projector import KnowledgeProjector

log = logging.getLogger(__name__)

# Callable shape for the semantic-document loader. Decoupled from
# SemanticOutputService so tests can inject a tiny lambda without
# constructing the whole service stack.
SemanticLoader = Callable[[str, str], SemanticDocument]


@dataclass(frozen=True)
class DriftedVersion:
    """A VALIDATED catalog version with no matching projection in the graph."""

    document_id: str
    version_id: str
    reason: str


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Result of reconciling one version.

    ``projection_ok`` reflects the projector run; ``entity_extraction_ok``
    is ``None`` when the extractor is not configured (Phase 2 disabled),
    ``True`` when the run succeeded, and ``False`` when it raised. The
    catalog is never mutated by reconciliation.
    """

    document_id: str
    version_id: str
    projection_ok: bool
    entity_extraction_ok: bool | None
    error: str | None = None


class KnowledgeLayerDisabled(RuntimeError):
    """Raised when reconciliation is invoked but the knowledge layer is off."""


class ReconciliationService:
    """Detect and repair drift between the catalog and the knowledge graph.

    Constructor is permissive on its dependencies — the projector and
    entity extractor are both optional so the service can be built from
    a :class:`PipelineServices` container that has the knowledge layer
    disabled. ``find_drifted_versions`` still works in that case (it
    will just report every VALIDATED version as drifted, since there is
    no projection); ``reconcile_version`` raises
    :class:`KnowledgeLayerDisabled` because there is nothing to project
    with.
    """

    def __init__(
        self,
        *,
        catalog: CatalogStore,
        graph_store: GraphStore,
        projector: KnowledgeProjector | None,
        entity_extractor: EntityExtractor | None,
        get_semantic: SemanticLoader,
    ) -> None:
        self._catalog = catalog
        self._graph_store = graph_store
        self._projector = projector
        self._entity_extractor = entity_extractor
        self._get_semantic = get_semantic

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def iter_validated_versions(self) -> Iterator[tuple[Document, DocumentVersion]]:
        """Yield every VALIDATED ``(document, version)`` pair in the catalog.

        Walks the catalog without paging — fine for the demo / small-deployment
        scale this CLI targets. If the catalog grows, swap this for a paging
        loop over ``catalog.list_documents(cursor=..., limit=...)``.
        """
        for document in self._catalog.list_documents():
            for version in document.versions:
                if version.status == DocumentVersionStatus.VALIDATED:
                    yield document, version

    def find_drifted_versions(self) -> list[DriftedVersion]:
        """Return every VALIDATED version whose projection is missing.

        A version is "drifted" when its ``(:Version)`` node — emitted by
        :meth:`KnowledgeProjector.project_document_structure` with
        ``id == version.id`` and ``kind == "version"`` — is absent from
        the graph for the version's document. Cached one read of
        ``find_subgraph_for_document`` per document so a family with
        many versions only pays the round-trip once.
        """
        drifted: list[DriftedVersion] = []
        cache_by_document: dict[str, set[str]] = {}
        for document, version in self.iter_validated_versions():
            present = cache_by_document.get(document.id)
            if present is None:
                projection = self._graph_store.find_subgraph_for_document(document.id)
                present = {node.id for node in projection.nodes if node.kind == "version"}
                cache_by_document[document.id] = present
            if version.id not in present:
                drifted.append(
                    DriftedVersion(
                        document_id=document.id,
                        version_id=version.id,
                        reason="version node missing from graph",
                    )
                )
        return drifted

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def reconcile_version(self, *, document_id: str, version_id: str) -> ReconciliationOutcome:
        """Re-run projection (and entity extraction if configured).

        Idempotent — the projector always does a delete-then-upsert on the
        version's nodes/edges, so calling this against an already-healthy
        version is harmless. Errors are caught per-stage so a projection
        success + extraction failure produces a partial-success outcome
        rather than throwing.
        """
        if self._projector is None:
            raise KnowledgeLayerDisabled(
                "reconcile_version requires a configured KnowledgeProjector; "
                "set KW_KNOWLEDGE_LAYER_ENABLED=true (and the KW_NEO4J_* env "
                "vars for Neo4j) before invoking."
            )

        document = self._catalog.get_document(document_id)
        if document is None:
            raise LookupError(f"Document {document_id!r} not found.")
        version = next((v for v in document.versions if v.id == version_id), None)
        if version is None:
            raise LookupError(f"Version {version_id!r} not found in document {document_id!r}.")
        if version.status != DocumentVersionStatus.VALIDATED:
            raise ValueError(
                f"Version {version_id!r} is in {version.status.value}, "
                "not VALIDATED; reconciliation is only defined for the "
                "post-validate side-effects."
            )

        try:
            semantic: SemanticDocument = self._get_semantic(document_id, version_id)
        except KeyError as exc:
            raise LookupError(
                f"No semantic document persisted for version {version_id!r}; cannot reconcile."
            ) from exc

        projection_ok = True
        extraction_ok: bool | None = None
        error: str | None = None

        try:
            self._projector.project(
                document=document,
                version=version,
                semantic=semantic,
            )
        except Exception as exc:  # noqa: BLE001 — record + return
            projection_ok = False
            error = f"projection failed: {exc!r}"
            log.exception(
                "knowledge.reconciliation.projection_failed",
                extra={"document_id": document_id, "version_id": version_id},
            )

        if projection_ok and self._entity_extractor is not None:
            try:
                result = self._entity_extractor.extract(
                    document=document,
                    version=version,
                    semantic=semantic,
                )
                self._projector.project_entities(result)
                extraction_ok = True
            except Exception as exc:  # noqa: BLE001
                extraction_ok = False
                error = f"entity extraction failed: {exc!r}"
                log.exception(
                    "knowledge.reconciliation.entity_extraction_failed",
                    extra={"document_id": document_id, "version_id": version_id},
                )

        log.info(
            "knowledge.reconciliation.completed",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "projection_ok": projection_ok,
                "entity_extraction_ok": extraction_ok,
            },
        )
        return ReconciliationOutcome(
            document_id=document_id,
            version_id=version_id,
            projection_ok=projection_ok,
            entity_extraction_ok=extraction_ok,
            error=error,
        )

    def reconcile_all_drifted(self) -> list[ReconciliationOutcome]:
        """Detect drift and reconcile every reported version, sequentially.

        Returns one outcome per attempt. Continues on error so a single bad
        version doesn't abort the whole batch; the caller decides what to
        report up.
        """
        outcomes: list[ReconciliationOutcome] = []
        for drift in self.find_drifted_versions():
            try:
                outcomes.append(
                    self.reconcile_version(
                        document_id=drift.document_id,
                        version_id=drift.version_id,
                    )
                )
            except (LookupError, ValueError, KnowledgeLayerDisabled) as exc:
                outcomes.append(
                    ReconciliationOutcome(
                        document_id=drift.document_id,
                        version_id=drift.version_id,
                        projection_ok=False,
                        entity_extraction_ok=None,
                        error=str(exc),
                    )
                )
        return outcomes
