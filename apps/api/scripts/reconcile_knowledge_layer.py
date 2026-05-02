#!/usr/bin/env python3
"""Reconcile drift between the catalog and the knowledge graph (#124).

ADR-012 §4 commits to fire-and-log semantics for the post-validate
side-effects: a Neo4j outage or LLM hiccup must not roll back validation.
The catalog is the source of truth, and the graph "catches up later".
This script *is* the "later" path.

Usage::

    # Detect: print every VALIDATED version whose projection is missing.
    python scripts/reconcile_knowledge_layer.py detect

    # Reconcile one version (re-run projection + entity extraction).
    python scripts/reconcile_knowledge_layer.py reconcile DOC_ID VER_ID

    # Reconcile every drifted version. Prints a summary and exits 1 if
    # any single reconciliation reported an error.
    python scripts/reconcile_knowledge_layer.py reconcile-all

The script reads the same env-driven settings as the API
(``KW_KNOWLEDGE_LAYER_ENABLED``, ``KW_NEO4J_*``, ``KW_PERSISTENT``,
``KW_DATA_DIR`` — see ``app.settings``) so it operates against the same
catalog + graph the live API uses. Run from ``apps/api/`` so the local
``.kw-pipeline/`` data directory resolves correctly.

Exit codes:

    0   detection found no drift / reconciliation reported all-OK.
    1   reconciliation reported any failure, or invalid arguments.
    2   knowledge layer disabled — set ``KW_KNOWLEDGE_LAYER_ENABLED=true``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import NoReturn

from app.dependencies import (
    PipelineServices,
    build_persistent_services,
    build_services,
)
from app.services.knowledge.reconciliation import (
    KnowledgeLayerDisabled,
    ReconciliationOutcome,
    ReconciliationService,
)
from app.settings import Settings


def _build_reconciler(services: PipelineServices) -> ReconciliationService:
    catalog = services.documents.catalog
    return ReconciliationService(
        catalog=catalog,
        graph_store=services.graph_store,
        projector=services.knowledge_projector,
        entity_extractor=services.entity_extractor,
        get_semantic=services.semantic_outputs.get,
    )


def _print_drift_table(drifted) -> None:  # type: ignore[no-untyped-def]
    if not drifted:
        print("No drifted versions detected.")
        return
    print(f"{len(drifted)} drifted version(s):")
    print(f"  {'document_id':<40}{'version_id':<40}reason")
    for entry in drifted:
        print(f"  {entry.document_id:<40}{entry.version_id:<40}{entry.reason}")


def _print_outcome(outcome: ReconciliationOutcome) -> None:
    payload = {
        "document_id": outcome.document_id,
        "version_id": outcome.version_id,
        "projection_ok": outcome.projection_ok,
        "entity_extraction_ok": outcome.entity_extraction_ok,
        "error": outcome.error,
    }
    print(json.dumps(payload, indent=2))


def _exit_for(outcomes: list[ReconciliationOutcome]) -> int:
    """Exit 0 only if every outcome is fully successful.

    A skipped extraction (``entity_extraction_ok is None`` because
    Phase 2 isn't configured) does not count as a failure.
    """
    for outcome in outcomes:
        if not outcome.projection_ok:
            return 1
        if outcome.entity_extraction_ok is False:
            return 1
    return 0


def main(argv: list[str] | None = None) -> NoReturn:
    parser = argparse.ArgumentParser(
        prog="reconcile_knowledge_layer",
        description=__doc__.split("\n\n", maxsplit=1)[0] if __doc__ else "",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="Print drifted VALIDATED versions and exit.")

    p_one = sub.add_parser(
        "reconcile",
        help="Re-run projection + entity extraction for one version.",
    )
    p_one.add_argument("document_id")
    p_one.add_argument("version_id")

    sub.add_parser(
        "reconcile-all",
        help="Detect drift then reconcile every reported version.",
    )

    args = parser.parse_args(argv)

    settings = Settings()
    services = (
        build_persistent_services(settings.data_dir, settings=settings)
        if settings.persistent
        else build_services(settings=settings)
    )
    reconciler = _build_reconciler(services)

    if args.command == "detect":
        _print_drift_table(reconciler.find_drifted_versions())
        sys.exit(0)

    if args.command == "reconcile":
        try:
            outcome = reconciler.reconcile_version(
                document_id=args.document_id,
                version_id=args.version_id,
            )
        except KnowledgeLayerDisabled as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
        except (LookupError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        _print_outcome(outcome)
        sys.exit(_exit_for([outcome]))

    if args.command == "reconcile-all":
        outcomes = reconciler.reconcile_all_drifted()
        if not outcomes:
            print("No drifted versions detected.")
            sys.exit(0)
        for outcome in outcomes:
            _print_outcome(outcome)
        ok_count = sum(
            1 for o in outcomes if o.projection_ok and o.entity_extraction_ok is not False
        )
        print(f"\n{ok_count} of {len(outcomes)} reconciled successfully.")
        sys.exit(_exit_for(outcomes))

    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    main()
