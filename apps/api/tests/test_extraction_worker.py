"""Tests for the in-process extraction worker harness (ADR-006, #40 PR-1).

Covers the queue contract (put/get/full), the worker lifecycle
(start/stop/idempotency), and the worker's behaviour under both
clean parses and parser failures. The harness is exercised directly
without going through ``app.main`` — the lifespan-level integration
is covered separately in ``test_extraction_worker_lifespan.py``.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.models.document import DocumentVersionStatus
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.extraction_worker import (
    ExtractionRequest,
    ExtractionWorker,
    InMemoryExtractionQueue,
    QueueFull,
)
from app.services.storage_service import InMemoryStorageService

# ── ExtractionRequest ────────────────────────────────────────────────


class TestExtractionRequest:
    def test_is_frozen(self) -> None:
        request = ExtractionRequest(document_id="doc-1", version_id="ver-1")
        with pytest.raises((AttributeError, TypeError)):
            request.document_id = "doc-2"  # type: ignore[misc]

    def test_uses_slots_for_low_overhead(self) -> None:
        request = ExtractionRequest(document_id="doc-1", version_id="ver-1")
        # ``slots=True`` means the instance has no ``__dict__``.
        assert not hasattr(request, "__dict__")


# ── InMemoryExtractionQueue ──────────────────────────────────────────


class TestInMemoryExtractionQueue:
    def test_rejects_zero_or_negative_maxsize(self) -> None:
        with pytest.raises(ValueError):
            InMemoryExtractionQueue(maxsize=0)
        with pytest.raises(ValueError):
            InMemoryExtractionQueue(maxsize=-1)

    def test_put_then_get_round_trip(self) -> None:
        async def scenario() -> ExtractionRequest:
            queue = InMemoryExtractionQueue(maxsize=4)
            await queue.put(ExtractionRequest(document_id="d", version_id="v"))
            assert queue.qsize() == 1
            assert not queue.is_full()
            return await queue.get()

        result = asyncio.run(scenario())
        assert result == ExtractionRequest(document_id="d", version_id="v")

    def test_put_raises_queue_full_at_capacity(self) -> None:
        async def scenario() -> None:
            queue = InMemoryExtractionQueue(maxsize=2)
            await queue.put(ExtractionRequest(document_id="d", version_id="v1"))
            await queue.put(ExtractionRequest(document_id="d", version_id="v2"))
            assert queue.is_full()
            with pytest.raises(QueueFull):
                await queue.put(ExtractionRequest(document_id="d", version_id="v3"))

        asyncio.run(scenario())

    def test_close_is_idempotent(self) -> None:
        async def scenario() -> None:
            queue = InMemoryExtractionQueue(maxsize=2)
            await queue.close()
            await queue.close()  # second call must not raise

        asyncio.run(scenario())


# ── ExtractionWorker ─────────────────────────────────────────────────


def _document_service_with_stored_version(
    *,
    filename: str = "note.txt",
    body: bytes = b"hello world",
) -> tuple[DocumentService, str, str]:
    """Build a document service holding one ``STORED`` version. Returns
    the service plus its ``(document_id, version_id)`` so the test can
    drive the worker against it."""
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload(filename, "text/plain", body)
    return documents, version.document_id, version.id


def _jobs(documents: DocumentService) -> ExtractionJobService:
    return ExtractionJobService(documents=documents, parsers=ParserRegistry([PlainTextParser()]))


class _AlwaysFailingParser:
    name = "always_failing"
    version = "test"
    supported_content_types = frozenset({"text/plain"})

    def parse(self, version, storage):
        raise RuntimeError("simulated parser failure")


class TestExtractionWorker:
    def test_drains_one_request_and_marks_extracted(self) -> None:
        documents, document_id, version_id = _document_service_with_stored_version()
        jobs = _jobs(documents)

        async def scenario() -> None:
            queue = InMemoryExtractionQueue(maxsize=4)
            worker = ExtractionWorker(queue=queue, jobs=jobs)
            await worker.start()
            try:
                await queue.put(ExtractionRequest(document_id=document_id, version_id=version_id))
                # Poll briefly for the FSM transition. Anything past ~50ms
                # in CI is symptomatic of the worker hanging on the
                # executor — fail loudly rather than wait the default
                # pytest timeout.
                for _ in range(50):
                    if (
                        documents.get_version(document_id, version_id).status
                        == DocumentVersionStatus.EXTRACTED
                    ):
                        return
                    await asyncio.sleep(0.01)
                pytest.fail("worker never transitioned the version to EXTRACTED")
            finally:
                await worker.stop()

        asyncio.run(scenario())

    def test_swallows_parser_failure_and_keeps_running(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # One STORED version + a parser that raises. The worker must
        # log + log to audit, but NOT die — a second job submitted
        # afterwards still gets processed.
        documents, document_id, bad_version_id = _document_service_with_stored_version()
        jobs = ExtractionJobService(
            documents=documents, parsers=ParserRegistry([_AlwaysFailingParser()])
        )

        async def scenario() -> None:
            queue = InMemoryExtractionQueue(maxsize=4)
            worker = ExtractionWorker(queue=queue, jobs=jobs)
            await worker.start()
            try:
                with caplog.at_level(logging.WARNING, logger="app.services.extraction_worker"):
                    await queue.put(
                        ExtractionRequest(document_id=document_id, version_id=bad_version_id)
                    )
                    # Wait for the FSM to flip to FAILED.
                    for _ in range(50):
                        if (
                            documents.get_version(document_id, bad_version_id).status
                            == DocumentVersionStatus.FAILED
                        ):
                            break
                        await asyncio.sleep(0.01)
                # Worker remains alive after the failure.
                assert worker.running, "worker died on parser failure"
                assert any(
                    record.message == "extraction.worker.job_failed" for record in caplog.records
                )
            finally:
                await worker.stop()

        asyncio.run(scenario())

    def test_start_and_stop_are_idempotent(self) -> None:
        documents, _, _ = _document_service_with_stored_version()
        jobs = _jobs(documents)

        async def scenario() -> None:
            queue = InMemoryExtractionQueue(maxsize=2)
            worker = ExtractionWorker(queue=queue, jobs=jobs)
            await worker.start()
            assert worker.running
            await worker.start()  # second start must not raise / spawn a duplicate task
            assert worker.running
            await worker.stop()
            assert not worker.running
            await worker.stop()  # second stop must not raise

        asyncio.run(scenario())

    def test_stop_without_start_is_a_noop(self) -> None:
        documents, _, _ = _document_service_with_stored_version()
        jobs = _jobs(documents)

        async def scenario() -> None:
            queue = InMemoryExtractionQueue(maxsize=2)
            worker = ExtractionWorker(queue=queue, jobs=jobs)
            await worker.stop()  # never started — must not raise

        asyncio.run(scenario())
