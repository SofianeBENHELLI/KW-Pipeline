import logging
from typing import Any

from app.models.document import DocumentVersionStatus
from app.schemas.extraction import RawExtraction
from app.services.document_parser import ParserRegistry
from app.services.document_service import DocumentService

log = logging.getLogger(__name__)


def _emit(event: str, payload: dict[str, Any], *, actor: str | None, level: int) -> None:
    """Emit a structured-log event with ``actor`` folded into ``extra`` only when set.

    Centralises the actor-conditional payload shape so every
    ``extraction.*`` emit site stays consistent — passing
    ``actor: None`` would land a ``null`` value in the audit JSON and
    confuse the :func:`event_actor` projection (and grep / jq).
    """
    extra = dict(payload)
    if actor is not None:
        extra["actor"] = actor
    log.log(level, event, extra=extra)


class ExtractionFailed(Exception):
    """Raised when a parser fails. Carries the persisted, human-readable reason
    so HTTP routes (and other callers) can surface the same string the catalog
    stored on the version."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ExtractionJobService:
    """Coordinates parser execution and extraction lifecycle transitions.

    Raw extraction JSON is persisted via the catalog store, so re-fetching
    a previously extracted version after a process restart returns the
    same payload without re-running the parser.

    Parser selection is delegated to a ``ParserRegistry`` keyed on
    ``DocumentVersion.content_type``. The registry decouples the job
    service from any specific parser implementation and lets a single
    deployment serve multiple content types. The legacy ``parser=`` shim
    introduced by #39 is dropped here — call sites must pass
    ``parsers=ParserRegistry([...])``.
    """

    def __init__(self, *, documents: DocumentService, parsers: ParserRegistry):
        self.documents = documents
        self.parsers = parsers

    def extract(
        self,
        document_id: str,
        version_id: str,
        *,
        actor: str | None = None,
    ) -> RawExtraction:
        """Run extraction for one stored, non-duplicate document version.

        ``actor`` is the authenticated principal id (ADR-019 §4); when
        provided, it lands on the ``extraction.started`` /
        ``extraction.succeeded`` / ``extraction.failed`` audit events
        plus the ``document.status_changed`` transitions emitted via
        ``mark_failed`` / ``update_status``. ``None`` is allowed for
        legacy / system callers (boot-time recovery, scripts) — the
        ``actor`` key is omitted from the audit payload in that case.
        """
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        if version.status == DocumentVersionStatus.DUPLICATE_DETECTED:
            raise ValueError("Duplicate versions are not extracted independently.")
        _emit(
            "extraction.started",
            {
                "document_id": document_id,
                "version_id": version_id,
                "content_type": version.content_type,
                "bytes_in": version.file_size,
            },
            actor=actor,
            level=logging.INFO,
        )
        self.documents.update_status(
            document_id, version_id, DocumentVersionStatus.EXTRACTING, actor=actor
        )
        try:
            parser = self.parsers.for_content_type(version.content_type)
        except KeyError as exc:
            reason = (
                str(exc.args[0])
                if exc.args
                else f"No parser for content_type: {version.content_type}"
            )
            self.documents.mark_failed(document_id, version_id, reason, actor=actor)
            _emit(
                "extraction.failed",
                {
                    "document_id": document_id,
                    "version_id": version_id,
                    "parser_name": None,
                    "failure_reason": reason,
                },
                actor=actor,
                level=logging.WARNING,
            )
            raise ExtractionFailed(reason) from exc
        # Use the parser's declared ``name`` so the audit log uses the
        # same identifier as ``RawExtraction.parser_name`` (e.g.
        # "plain_text", "docx", "pdf", "pptx"). Letting greppers join
        # logs and stored extractions on a single value (#26).
        parser_name = parser.name
        try:
            raw_extraction = parser.parse(version=version, storage=self.documents.storage)
        except Exception as exc:
            reason = f"{parser_name}: {exc}"
            self.documents.mark_failed(document_id, version_id, reason, actor=actor)
            _emit(
                "extraction.failed",
                {
                    "document_id": document_id,
                    "version_id": version_id,
                    "parser_name": parser_name,
                    "failure_reason": reason,
                },
                actor=actor,
                level=logging.WARNING,
            )
            raise ExtractionFailed(reason) from exc
        if not raw_extraction.source_references:
            reason = f"{parser_name}: No extractable content"
            self.documents.mark_failed(document_id, version_id, reason, actor=actor)
            _emit(
                "extraction.failed",
                {
                    "document_id": document_id,
                    "version_id": version_id,
                    "parser_name": parser_name,
                    "failure_reason": reason,
                },
                actor=actor,
                level=logging.WARNING,
            )
            raise ExtractionFailed(reason)
        self.documents.catalog.save_raw_extraction(version_id, raw_extraction)
        self.documents.update_status(
            document_id, version_id, DocumentVersionStatus.EXTRACTED, actor=actor
        )
        _emit(
            "extraction.succeeded",
            {
                "document_id": document_id,
                "version_id": version_id,
                "parser_name": parser_name,
                "bytes_in": version.file_size,
                "sections_out": len(raw_extraction.source_references),
            },
            actor=actor,
            level=logging.INFO,
        )
        return raw_extraction

    def retry_extract(
        self,
        document_id: str,
        version_id: str,
        *,
        actor: str | None = None,
    ) -> RawExtraction:
        """Retry extraction for a previously-FAILED document version (#87).

        The MVP recovery surface for "an extraction failed because of an
        unsupported parser, transient infrastructure error, or bad
        configuration; after the issue is fixed, retry without re-
        uploading everything." Concretely:

        * The version's current status MUST be ``FAILED``. Any other
          status (``EXTRACTED``, ``VALIDATED``, ``REJECTED``,
          ``DUPLICATE_DETECTED``, …) raises :class:`ValueError` so the
          route layer surfaces a 409 — retry never bypasses the review
          gate or reprocesses a still-running pipeline.
        * Emits an :data:`extraction.retried` audit event before re-
          running so the structured log preserves a clean retry trail
          (the previous ``extraction.failed`` records aren't deleted —
          they just sit ahead of the new ``extraction.started`` /
          ``extraction.succeeded`` entries on the timeline).
        * Delegates to :meth:`extract` for the actual run. The FSM has
          a dedicated ``FAILED → EXTRACTING`` edge (see
          ``app.models.document.ALLOWED_TRANSITIONS``) so the inner
          ``update_status`` call goes through cleanly. On success, the
          catalog clears the previous ``failure_reason`` (see
          :meth:`CatalogStore.update_version_status`); on re-fail, the
          new reason replaces the old one and the version stays
          ``FAILED``.
        """
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        if version.status is not DocumentVersionStatus.FAILED:
            raise ValueError(
                f"Retry only allowed from FAILED; version is currently {version.status.value}."
            )
        _emit(
            "extraction.retried",
            {
                "document_id": document_id,
                "version_id": version_id,
                "previous_failure_reason": version.failure_reason,
            },
            actor=actor,
            level=logging.INFO,
        )
        return self.extract(document_id=document_id, version_id=version_id, actor=actor)

    def get_raw_extraction(self, document_id: str, version_id: str) -> RawExtraction:
        """Return raw extraction output for a document version."""
        self.documents.get_version(document_id=document_id, version_id=version_id)
        return self.documents.catalog.get_raw_extraction(version_id)
