"""``kw-rebackfill`` — re-extract legacy PDF versions under the new
line-level parser.

Phase 1 of the PDF-viewer roadmap bumped :class:`PdfParser` from
``parser_version=0.1`` (one section per page, no rects) to ``0.2``
(one section per heading-run-of-lines with normalised rects). The
Phase 5 contract is "new uploads only" — existing rows stay on the
old shape until an operator opts in. This CLI is that opt-in.

What the CLI does, per matching version:

1. Re-runs :class:`PdfParser` against the originally-uploaded bytes.
2. Persists the new :class:`RawExtraction` (replaces the JSON payload
   in ``raw_extractions``).
3. Re-runs :class:`SemanticExtractor` + :class:`MarkdownGenerator`
   and persists the new :class:`SemanticDocument`.
4. Deletes any persisted claims and document-topics for the version —
   their ``supporting_chunk_ids`` reference the OLD section ids and
   would otherwise be unverifiable.
5. If the version was ``VALIDATED``, demotes it back to
   ``NEEDS_REVIEW`` through :class:`ReviewService` so a reviewer
   re-acknowledges the new chunk shape and the knowledge-graph
   projection rebuilds against the new ids. ``REJECTED`` versions
   are also demoted so the same re-review path applies.

Knowledge-graph entities and Neo4j projection are intentionally not
touched here — they rebuild on next validate (delete-then-upsert per
ADR-012). Keeping the CLI focused on the catalog side avoids a Neo4j
dependency at the command line.

Usage:

    python -m app.rebackfill [--dry-run] [--document-id DOC_ID] [--limit N]

    .venv312/bin/kw-rebackfill [--dry-run] [--document-id DOC_ID] [--limit N]

A dry run prints the per-version action plan without writing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.dependencies import PipelineServices, build_services
from app.models.document import DocumentVersionStatus
from app.schemas.document import DocumentVersion
from app.services.parsers.pdf import PDF_CONTENT_TYPE, PdfParser

# Parser versions strictly below this string get re-extracted.
_TARGET_PARSER_VERSION = "0.2"

# Reviewer note recorded on the audit trail when the CLI demotes a
# previously-validated row. Operators searching audit events can filter
# on this exact string to find every version touched by the backfill.
_DEMOTE_NOTE = "Rebackfilled by kw-rebackfill: re-extract with line-level PDF parser (0.2)."

log = logging.getLogger(__name__)


@dataclass(slots=True)
class VersionPlan:
    """One row in the action plan."""

    document_id: str
    version_id: str
    filename: str
    current_status: DocumentVersionStatus
    current_parser_version: str
    will_demote: bool


@dataclass(slots=True)
class RebackfillResult:
    """Aggregate summary the CLI prints + tests assert against."""

    scanned_versions: int = 0
    eligible_versions: int = 0
    rebackfilled: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False
    plan: list[VersionPlan] = field(default_factory=list)


def _eligible_versions(
    *,
    services: PipelineServices,
    document_id_filter: str | None,
    limit: int | None,
) -> list[tuple[str, DocumentVersion, str]]:
    """Return ``(document_id, version, parser_version)`` triples for
    PDF versions still on parser_version < 0.2.

    Versions without a persisted ``raw_extraction`` are skipped — there
    is nothing to re-extract from a row that never ran ``/extract``.
    """
    out: list[tuple[str, DocumentVersion, str]] = []
    for document in services.documents.list_documents():
        if document_id_filter is not None and document.id != document_id_filter:
            continue
        for version in document.versions:
            if version.content_type != PDF_CONTENT_TYPE:
                continue
            try:
                raw = services.documents.catalog.get_raw_extraction(version.id)
            except KeyError:
                continue
            parser_version = raw.parser_version
            if parser_version >= _TARGET_PARSER_VERSION:
                continue
            out.append((document.id, version, parser_version))
            if limit is not None and len(out) >= limit:
                return out
    return out


def _delete_llm_artifacts_for_version(
    *,
    services: PipelineServices,
    version_id: str,
) -> None:
    """Remove claim + topic rows that cite the (now-stale) old chunk ids.

    Both stores expose idempotent ``delete_for_version`` per the cascade-
    deletion pattern shipped with the original stores.
    """
    services.claim_store.delete_for_version(version_id)
    services.document_topic_store.delete_for_version(version_id)


def _rebackfill_one(
    *,
    services: PipelineServices,
    document_id: str,
    version: DocumentVersion,
    actor: str,
) -> bool:
    """Re-extract one version. Returns True when a demote-to-review
    was applied on top of the re-extraction."""
    parser = PdfParser()
    raw = parser.parse(version=version, storage=services.documents.storage)
    services.documents.catalog.save_raw_extraction(version.id, raw)

    semantic = services.semantic_extractor.extract(version=version, raw_extraction=raw)
    semantic.markdown = services.markdown_generator.render(
        version=version,
        semantic=semantic,
        raw_extraction=raw,
    )
    services.documents.catalog.save_semantic_document(version.id, semantic)

    _delete_llm_artifacts_for_version(services=services, version_id=version.id)

    if version.status in (
        DocumentVersionStatus.VALIDATED,
        DocumentVersionStatus.REJECTED,
    ):
        services.review.handle_demote_to_review(
            document_id=document_id,
            version_id=version.id,
            reviewer_note=_DEMOTE_NOTE,
            actor=actor,
        )
        return True
    return False


def run_rebackfill(
    *,
    services: PipelineServices,
    dry_run: bool = False,
    document_id: str | None = None,
    limit: int | None = None,
    actor: str = "kw-rebackfill",
) -> RebackfillResult:
    """Headless entry point used by both ``main`` and the test suite."""
    result = RebackfillResult(dry_run=dry_run)
    eligible = _eligible_versions(
        services=services,
        document_id_filter=document_id,
        limit=limit,
    )
    result.scanned_versions = sum(
        len(d.versions)
        for d in services.documents.list_documents()
        if document_id is None or d.id == document_id
    )
    result.eligible_versions = len(eligible)

    for doc_id, version, parser_version in eligible:
        plan = VersionPlan(
            document_id=doc_id,
            version_id=version.id,
            filename=version.filename,
            current_status=version.status,
            current_parser_version=parser_version,
            will_demote=version.status
            in (
                DocumentVersionStatus.VALIDATED,
                DocumentVersionStatus.REJECTED,
            ),
        )
        result.plan.append(plan)

        if dry_run:
            continue

        try:
            demoted = _rebackfill_one(
                services=services,
                document_id=doc_id,
                version=version,
                actor=actor,
            )
        except Exception as exc:  # noqa: BLE001 — surface every failure to the operator
            log.exception(
                "rebackfill.failed",
                extra={"document_id": doc_id, "version_id": version.id},
            )
            result.skipped.append((version.id, f"{type(exc).__name__}: {exc}"))
            continue

        result.rebackfilled.append(version.id)
        if demoted:
            result.demoted.append(version.id)

    log.info(
        "pdf.rebackfill.completed",
        extra={
            "dry_run": dry_run,
            "scanned_versions": result.scanned_versions,
            "eligible_versions": result.eligible_versions,
            "rebackfilled_count": len(result.rebackfilled),
            "demoted_count": len(result.demoted),
            "skipped_count": len(result.skipped),
            "document_id_filter": document_id,
            "limit": limit,
        },
    )
    return result


def _print_summary(result: RebackfillResult, *, stream: Any = sys.stdout) -> None:
    print(
        (
            "Scanned {scanned} version(s); {eligible} eligible "
            "(PDF + parser_version < {target}).".format(
                scanned=result.scanned_versions,
                eligible=result.eligible_versions,
                target=_TARGET_PARSER_VERSION,
            )
        ),
        file=stream,
    )
    if not result.plan:
        return

    print("\nAction plan:", file=stream)
    for entry in result.plan:
        suffix = " → NEEDS_REVIEW" if entry.will_demote else ""
        print(
            f"  - {entry.document_id} / {entry.version_id} "
            f"({entry.filename}, was {entry.current_status.value}, "
            f"parser={entry.current_parser_version}){suffix}",
            file=stream,
        )

    if result.dry_run:
        print("\n(dry run — no changes written)", file=stream)
        return

    print(
        f"\nRebackfilled {len(result.rebackfilled)} version(s); "
        f"demoted {len(result.demoted)} to NEEDS_REVIEW; "
        f"skipped {len(result.skipped)} on error.",
        file=stream,
    )
    for version_id, reason in result.skipped:
        print(f"  ! {version_id}: {reason}", file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kw-rebackfill",
        description=(
            "Re-extract legacy PDF versions to the parser_version 0.2 shape "
            "(line-level sections with normalised rects)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the action plan without writing any changes.",
    )
    parser.add_argument(
        "--document-id",
        default=None,
        help="Only consider versions of this document family.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N versions in this run.",
    )
    parser.add_argument(
        "--actor",
        default="kw-rebackfill",
        help=(
            "Audit-log actor recorded on the demote-to-review event. "
            "Defaults to the CLI name."
        ),
    )
    args = parser.parse_args(argv)

    services = build_services()
    result = run_rebackfill(
        services=services,
        dry_run=args.dry_run,
        document_id=args.document_id,
        limit=args.limit,
        actor=args.actor,
    )
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
