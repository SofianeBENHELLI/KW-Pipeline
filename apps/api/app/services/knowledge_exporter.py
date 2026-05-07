"""Knowledge export service — assemble a deterministic
:class:`KnowledgeExportPackage` from the catalog + semantic store
(closes #23).

The exporter is a small composition layer: it reads
:class:`DocumentVersion` metadata from :class:`DocumentService`,
reads the persisted :class:`SemanticDocument` via
:class:`SemanticOutputService`, and projects them onto the
content-addressed wire shape documented in
``docs/architecture/knowledge_export_contract.md``.

Determinism is the single non-negotiable property: re-exporting the
same version twice must yield byte-identical chunks/assets so
``manifest.package_sha256`` is a usable consumer-side cache key. All
ordering and ID generation happen here; the schema in
:mod:`app.schemas.knowledge_export` only describes the shape.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import TYPE_CHECKING

from app.schemas.knowledge_export import (
    ExportedAsset,
    ExportedChunk,
    ExportManifest,
    KnowledgeExportPackage,
)
from app.schemas.semantic_document import (
    SemanticAsset,
    SemanticDocument,
    SemanticSection,
)

if TYPE_CHECKING:
    from app.schemas.document import DocumentVersion
    from app.services.document_service import DocumentService
    from app.services.semantic_output_service import SemanticOutputService


# Whitespace runs (spaces, tabs, newlines, NBSP, …) collapse to a
# single ASCII space. The pattern matches one or more characters
# whose Unicode category is "Z*" (separator) plus the ASCII
# whitespace controls — the `re.UNICODE` flag is the default in
# Python 3 so ``\s`` already covers the common cases.
_WHITESPACE_RUN = re.compile(r"\s+", re.UNICODE)


def _normalize_text(text: str) -> str:
    """Normalize a chunk/asset text for content-addressed hashing.

    NFKC-normalize, strip leading/trailing whitespace, then collapse
    every internal whitespace run to a single ASCII space. The result
    is what the deterministic IDs hash, *not* what the package emits —
    the original text rides through to consumers verbatim. The point
    is to keep the cache key stable across line-wrapping tweaks the
    parser may introduce on a re-extraction.
    """
    nfkc = unicodedata.normalize("NFKC", text)
    return _WHITESPACE_RUN.sub(" ", nfkc.strip())


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk_id_for(*, document_id: str, version_id: str, normalized_text: str) -> str:
    """Deterministic chunk handle: ``chunk_<first 16 hex of sha256>``.

    Salted with ``(document_id, version_id)`` so identical boilerplate
    sections across documents don't collide. The full sha256 (256 bit)
    is exposed separately on ``ExportedChunk.content_sha256`` for
    consumers that want a longer handle.
    """
    payload = f"{document_id}/{version_id}/{normalized_text}".encode()
    return "chunk_" + _sha256_hex(payload)[:16]


def _asset_id_for(*, version_id: str, asset_type: str, normalized_text: str) -> str:
    """Deterministic asset handle. Salted with ``version_id`` plus the
    asset type so two assets of different types extracted from the
    same paragraph get distinct ids.
    """
    payload = f"{version_id}/{asset_type}/{normalized_text}".encode()
    return "asset_" + _sha256_hex(payload)[:16]


def _project_chunk(
    *,
    section: SemanticSection,
    document_id: str,
    version_id: str,
    validation_status: str,
) -> ExportedChunk:
    text = section.text or ""
    normalized = _normalize_text(text)
    content_sha256 = _sha256_hex(normalized.encode("utf-8"))
    return ExportedChunk(
        chunk_id=_chunk_id_for(
            document_id=document_id,
            version_id=version_id,
            normalized_text=normalized,
        ),
        section_id=section.id,
        document_id=document_id,
        document_version_id=version_id,
        heading=section.heading,
        text=text,
        char_count=len(text),
        content_sha256=content_sha256,
        source_reference_ids=list(section.source_reference_ids),
        # ``ValidationStatus`` is the closed Literal; cast through
        # the type system via Pydantic's coercion.
        validation_status=validation_status,  # type: ignore[arg-type]
    )


def _project_asset(*, asset: SemanticAsset, version_id: str) -> ExportedAsset:
    text = asset.text or ""
    normalized = _normalize_text(text)
    content_sha256 = _sha256_hex(normalized.encode("utf-8"))
    return ExportedAsset(
        asset_id=_asset_id_for(
            version_id=version_id,
            asset_type=asset.type,
            normalized_text=normalized,
        ),
        asset_type=asset.type,
        text=text,
        confidence=asset.confidence,
        review_status=asset.review_status,
        source_reference_ids=list(asset.source_reference_ids),
        content_sha256=content_sha256,
    )


def _package_sha256(chunks: list[ExportedChunk], assets: list[ExportedAsset]) -> str:
    """Canonical-JSON sha256 of the chunks/assets pair.

    Sorting at the top level (by ``chunk_id``, ``asset_id``) makes the
    hash reorder-stable: rebuilding the package from a different walk
    order of the source semantic document yields the same hash. We use
    Pydantic's ``model_dump(mode="json")`` so datetimes / enums fold
    to their JSON form before hashing.
    """
    chunk_payload = [c.model_dump(mode="json") for c in sorted(chunks, key=lambda c: c.chunk_id)]
    asset_payload = [a.model_dump(mode="json") for a in sorted(assets, key=lambda a: a.asset_id)]
    canonical = json.dumps(
        {"chunks": chunk_payload, "assets": asset_payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _sha256_hex(canonical)


class KnowledgeExporter:
    """Build a deterministic export package for one document version.

    Construction is cheap; the service holds references to the catalog
    and semantic stores but no per-call state. The instance is safe to
    cache at app boot and call concurrently.
    """

    def __init__(
        self,
        *,
        documents: DocumentService,
        semantic_outputs: SemanticOutputService,
    ) -> None:
        self.documents = documents
        self.semantic_outputs = semantic_outputs

    def export(self, *, document_id: str, version_id: str) -> KnowledgeExportPackage:
        """Build the export package for ``(document_id, version_id)``.

        Raises whatever the underlying stores raise on missing data —
        the caller (typically the future #90 handoff route) is
        responsible for translating those into HTTP error envelopes.
        Re-running ``export`` on the same version yields a package
        with an identical ``manifest.package_sha256`` even if the
        in-memory ordering of sections/assets differs across calls.
        """
        document = self.documents.get_document(document_id)
        if document is None:
            raise KeyError(f"Document {document_id!r} not found; cannot build export package.")
        version: DocumentVersion = self.documents.get_version(document_id, version_id)
        semantic = self.semantic_outputs.get(document_id=document_id, version_id=version_id)
        return self._build(
            document_or_filename=document.original_filename,
            version=version,
            semantic=semantic,
        )

    def _build(
        self,
        *,
        document_or_filename: str,
        version: DocumentVersion,
        semantic: SemanticDocument,
    ) -> KnowledgeExportPackage:
        chunks = [
            _project_chunk(
                section=s,
                document_id=version.document_id,
                version_id=version.id,
                validation_status=semantic.validation_status,
            )
            for s in semantic.sections
        ]
        assets = [_project_asset(asset=a, version_id=version.id) for a in semantic.assets]
        package_sha256 = _package_sha256(chunks, assets)
        # ``schema_version`` is the closed-Literal default on the model,
        # so we leave it implicit rather than re-passing the constant
        # (mypy treats the kwarg as ``str`` in the call site).
        manifest = ExportManifest(
            document_id=version.document_id,
            document_version_id=version.id,
            document_version_number=version.version_number,
            original_filename=document_or_filename,
            version_filename=version.filename,
            document_sha256=version.sha256,
            content_type=version.content_type,
            semantic_schema_version=semantic.schema_version,
            validation_status=semantic.validation_status,
            document_type=semantic.document_profile.document_type,
            chunk_count=len(chunks),
            asset_count=len(assets),
            package_sha256=package_sha256,
        )
        return KnowledgeExportPackage(
            manifest=manifest,
            chunks=chunks,
            assets=assets,
            markdown=semantic.markdown,
        )


__all__ = ["KnowledgeExporter"]
