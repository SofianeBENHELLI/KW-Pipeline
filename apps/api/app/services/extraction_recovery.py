"""Boot-time recovery for the async extraction queue (ADR-006 §5).

The MVP queue is in-process and non-persistent: a ``docker restart``
between FSM-flip and worker dequeue leaves the affected version stuck
in ``EXTRACTING`` (and, once PR-2 lands, ``QUEUED_FOR_EXTRACTION``)
with no worker attached. This helper runs once on app boot, scans for
those stuck versions, flips them to ``FAILED`` with a clear reason,
and lets the operator recover via the existing
``POST /documents/.../retry-extraction`` route.

The scan is fail-soft: any exception is logged and swallowed so a
boot-time SQLite blip doesn't keep the API from accepting Phase 1 /
Phase 2 traffic. A best-effort recovery is better than no recovery.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.models.document import DocumentVersionStatus

if TYPE_CHECKING:
    from app.dependencies import PipelineServices

log = logging.getLogger(__name__)


_STUCK_REASON = "Extraction interrupted by process restart."


def recover_stuck_extractions(services: PipelineServices) -> int:
    """Flip every ``EXTRACTING`` version to ``FAILED`` with a clear reason.

    Returns the number of versions recovered. The MVP scope handles
    ``EXTRACTING`` only — PR-2 will widen the scan to also cover the
    new ``QUEUED_FOR_EXTRACTION`` state once that FSM transition lands.

    Skipped (and ``0`` returned) when ``settings.extraction_inline`` is
    ``True``: inline mode never enqueues, so a stuck-state recovery is
    a waste of boot time and could mask a genuine inline-extraction bug.
    Operators flipping to async mode (PR-3) automatically pick up the
    scan on the next boot.
    """
    settings = services.settings
    if settings.extraction_inline:
        return 0

    catalog = services.documents.catalog
    stuck_states = frozenset({DocumentVersionStatus.EXTRACTING})
    try:
        documents = catalog.list_documents(status_filter=stuck_states)
    except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
        log.warning(
            "extraction.recovery.scan_failed",
            extra={"error_type": type(exc).__name__},
        )
        return 0

    recovered = 0
    for document in documents:
        for version in document.versions:
            if version.status is not DocumentVersionStatus.EXTRACTING:
                continue
            try:
                services.documents.mark_failed(
                    document.id,
                    version.id,
                    _STUCK_REASON,
                )
            except Exception as exc:  # noqa: BLE001 - fire-and-log per-row
                # Per-version failure: log and keep going so one bad row
                # doesn't deny the rest of the queue a clean recovery.
                log.warning(
                    "extraction.recovery.mark_failed_failed",
                    extra={
                        "document_id": document.id,
                        "version_id": version.id,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            recovered += 1
            log.info(
                "extraction.recovery.recovered",
                extra={
                    "document_id": document.id,
                    "version_id": version.id,
                    "reason": _STUCK_REASON,
                },
            )

    if recovered > 0:
        log.warning(
            "extraction.recovery.summary",
            extra={"recovered_count": recovered},
        )

    return recovered


__all__ = ["recover_stuck_extractions"]
