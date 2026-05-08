"""Route-level tests for async extraction (ADR-006, #40 PR-2).

PR-2 adds:

- A new ``QUEUED_FOR_EXTRACTION`` FSM state with ``{STORED, FAILED}``
  predecessors and ``{EXTRACTING, FAILED}`` successors.
- A new :class:`ExtractionJobSnapshot` schema returned with HTTP 202
  by ``POST /documents/.../extract`` and ``…/retry-extraction`` when
  ``KW_EXTRACTION_INLINE=false``.
- A 503 ``KW_QUEUE_FULL`` envelope with ``Retry-After: 5`` when the
  bounded :class:`asyncio.Queue` is at capacity.

Inline mode (the default until PR-3) must keep returning the
``RawExtraction`` 200 contract without behaviour changes — the
existing ``test_extraction_worker_lifespan.py`` covers that path; the
tests below add the async-mode coverage.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.extraction import ExtractionJobSnapshot

PLAIN = "text/plain"


# ─── Fixtures / helpers ───────────────────────────────────────────────


def _services_with(*, extraction_inline: bool, queue_size: int = 4, workers: int = 1):
    """Build a fresh :class:`PipelineServices` and override the async-extraction knobs.

    ``object.__setattr__`` is the same trick the existing lifespan tests
    use — :class:`pydantic_settings.BaseSettings` doesn't ban mutation
    but the ``model_config`` keys we don't want to touch (env aliases)
    stay set.
    """
    services = build_services()
    object.__setattr__(services.settings, "extraction_inline", extraction_inline)
    object.__setattr__(services.settings, "extraction_queue_size", queue_size)
    object.__setattr__(services.settings, "extraction_workers", workers)
    return services


def _upload(client: TestClient, *, body: bytes = b"hello world") -> dict:
    response = client.post(
        "/documents/upload",
        files={"file": ("note.txt", body, PLAIN)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_status(
    client: TestClient,
    *,
    document_id: str,
    version_id: str,
    target: set[DocumentVersionStatus],
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> str:
    """Poll ``GET /documents/{id}`` until the version reaches one of ``target``.

    Returns the final status value. Raises :class:`AssertionError` when
    the timeout elapses — keeps the failure mode loud rather than
    hanging the suite.
    """
    deadline = time.monotonic() + timeout
    target_values = {status.value for status in target}
    while time.monotonic() < deadline:
        response = client.get(f"/documents/{document_id}")
        assert response.status_code == 200, response.text
        document = response.json()
        for version in document["versions"]:
            if version["id"] == version_id and version["status"] in target_values:
                return version["status"]
        time.sleep(poll_interval)
    raise AssertionError(
        f"Version {version_id} did not reach any of {target_values} within {timeout}s."
    )


# ─── Inline mode: contract preserved ──────────────────────────────────


def test_inline_mode_extract_route_returns_200_and_raw_extraction() -> None:
    """``KW_EXTRACTION_INLINE=true`` (default) keeps the pre-PR-2 contract:
    POST /…/extract runs synchronously and returns 200 with
    :class:`RawExtraction`."""
    services = _services_with(extraction_inline=True)
    app = create_app(services=services)

    with TestClient(app) as client:
        version = _upload(client)
        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # ``RawExtraction`` carries ``parser_name`` and ``sections`` —
        # the snapshot shape would have ``job_id`` / ``queue_position``.
        assert body["parser_name"] == "plain_text"
        assert "sections" in body
        assert "job_id" not in body


# ─── Async mode: 202 + ExtractionJobSnapshot ──────────────────────────


def test_async_mode_extract_route_returns_202_and_snapshot() -> None:
    """``KW_EXTRACTION_INLINE=false`` enqueues the job and returns 202
    with an :class:`ExtractionJobSnapshot` body whose status is
    ``QUEUED_FOR_EXTRACTION`` and whose ``queue_position`` is ``>= 1``
    at submission time."""
    # 1 worker but a tiny queue — keep submission deterministic by
    # stalling the worker via a slow registry replacement.
    services = _services_with(extraction_inline=False, queue_size=8, workers=1)

    # Stall the worker by swapping in a parser that blocks. We want to
    # observe the ``QUEUED_FOR_EXTRACTION`` snapshot before the worker
    # picks up the job.
    import threading

    block = threading.Event()

    class _BlockingParser:
        name = "blocking_plain"
        version = "test"
        supported_content_types = frozenset({PLAIN})

        def parse(self, version, storage):  # noqa: ANN001
            from app.schemas.extraction import RawExtraction, RawSection, SourceReference

            block.wait(timeout=5.0)
            content = storage.get(version.storage_uri).decode("utf-8")
            ref = SourceReference(
                document_version_id=version.id,
                section_id="s-0",
                snippet=content[:24],
            )
            return RawExtraction(
                document_version_id=version.id,
                parser_name=self.name,
                parser_version=self.version,
                text=content,
                sections=[
                    RawSection(
                        id="s-0",
                        heading="Body",
                        text=content,
                        source_reference_ids=[ref.id],
                    )
                ],
                source_references=[ref],
            )

    app = create_app(services=services)
    services.parsers._by_content_type[PLAIN] = _BlockingParser()  # type: ignore[attr-defined]

    try:
        with TestClient(app) as client:
            version = _upload(client)
            response = client.post(
                f"/documents/{version['document_id']}/versions/{version['id']}/extract",
            )
            assert response.status_code == 202, response.text
            body = response.json()
            ExtractionJobSnapshot.model_validate(body)  # round-trip check
            assert body["job_id"] == f"ext-{version['id']}"
            assert body["status"] == DocumentVersionStatus.QUEUED_FOR_EXTRACTION.value
            assert body["document_id"] == version["document_id"]
            assert body["version_id"] == version["id"]
            assert isinstance(body["queue_position"], int)
            assert body["queue_position"] >= 1

            # The version is observably ``QUEUED_FOR_EXTRACTION`` until
            # the worker dequeues — the blocking parser is still
            # waiting on ``block``.
            doc = client.get(f"/documents/{version['document_id']}").json()
            statuses = {v["id"]: v["status"] for v in doc["versions"]}
            # The version should be QUEUED or already EXTRACTING (the
            # worker may have dequeued and entered the parser).
            assert statuses[version["id"]] in {
                DocumentVersionStatus.QUEUED_FOR_EXTRACTION.value,
                DocumentVersionStatus.EXTRACTING.value,
            }
    finally:
        # Always release the parser so the worker shutdown is fast even
        # if an assertion above failed.
        block.set()


def test_async_mode_full_pipeline_reaches_extracted() -> None:
    """End-to-end async path: kick the route, then poll
    ``GET /documents/{id}`` until the version reaches ``EXTRACTED``."""
    services = _services_with(extraction_inline=False, queue_size=4, workers=1)
    app = create_app(services=services)

    with TestClient(app) as client:
        version = _upload(client, body=b"first\nsecond")
        response = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        )
        assert response.status_code == 202, response.text
        final = _wait_for_status(
            client,
            document_id=version["document_id"],
            version_id=version["id"],
            target={DocumentVersionStatus.EXTRACTED},
        )
        assert final == DocumentVersionStatus.EXTRACTED.value


def test_async_mode_extract_route_returns_503_when_queue_is_full() -> None:
    """When the bounded queue is at capacity the route returns 503 with
    ``Retry-After: 5`` and the ``KW_QUEUE_FULL`` error envelope.

    Strategy: wedge the single worker on a blocking parser so the
    first job stays in flight, fill the queue (size=1), then submit
    a second job. The second submission must trip the
    ``QueueFull → 503`` branch.
    """
    services = _services_with(extraction_inline=False, queue_size=1, workers=1)

    import threading

    block = threading.Event()

    class _BlockingParser:
        name = "blocking_plain"
        version = "test"
        supported_content_types = frozenset({PLAIN})

        def parse(self, version, storage):  # noqa: ANN001
            block.wait(timeout=10.0)
            from app.schemas.extraction import RawExtraction, RawSection, SourceReference

            content = storage.get(version.storage_uri).decode("utf-8") or "x"
            ref = SourceReference(
                document_version_id=version.id,
                section_id="s-0",
                snippet=content[:24],
            )
            return RawExtraction(
                document_version_id=version.id,
                parser_name=self.name,
                parser_version=self.version,
                text=content,
                sections=[
                    RawSection(
                        id="s-0",
                        heading="Body",
                        text=content,
                        source_reference_ids=[ref.id],
                    )
                ],
                source_references=[ref],
            )

    app = create_app(services=services)
    services.parsers._by_content_type[PLAIN] = _BlockingParser()  # type: ignore[attr-defined]

    try:
        with TestClient(app) as client:
            v1 = _upload(client, body=b"first")
            v2 = _upload(client, body=b"second")
            v3 = _upload(client, body=b"third")

            # First submission: occupies the worker (parser blocks).
            r1 = client.post(
                f"/documents/{v1['document_id']}/versions/{v1['id']}/extract",
            )
            assert r1.status_code == 202, r1.text

            # Give the worker a moment to dequeue and enter the parser
            # — otherwise both jobs would slot into the size-1 queue.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                doc = client.get(f"/documents/{v1['document_id']}").json()
                if any(
                    v["id"] == v1["id"] and v["status"] == "EXTRACTING" for v in doc["versions"]
                ):
                    break
                time.sleep(0.02)

            # Second submission: lands in the size-1 queue.
            r2 = client.post(
                f"/documents/{v2['document_id']}/versions/{v2['id']}/extract",
            )
            assert r2.status_code == 202, r2.text

            # Third submission: queue is now full → 503 with Retry-After.
            r3 = client.post(
                f"/documents/{v3['document_id']}/versions/{v3['id']}/extract",
            )
            assert r3.status_code == 503, r3.text
            assert r3.headers.get("Retry-After") == "5"
            envelope = r3.json()
            assert envelope["error"]["code"] == "KW_QUEUE_FULL"
            assert envelope["error"]["retryable"] is True
    finally:
        block.set()


def test_async_mode_retry_route_returns_202_and_runs_to_completion() -> None:
    """Async retry path: a FAILED version transitions
    ``FAILED → QUEUED_FOR_EXTRACTION`` via the retry route, the worker
    drains the queue, and the version eventually reaches a terminal
    extraction state.

    The first extraction is forced to fail by having no parser
    registered for the content type. After widening the registry, the
    retry route returns 202 with the snapshot and the worker pushes
    the version to ``EXTRACTED``.
    """
    odd_mime = "application/x-pr2-retry"
    services = _services_with(extraction_inline=False, queue_size=4, workers=1)
    object.__setattr__(
        services.settings,
        "allowed_content_types_raw",
        f"{PLAIN},{odd_mime}",
    )
    app = create_app(services=services)

    with TestClient(app) as client:
        upload = client.post(
            "/documents/upload",
            files={"file": ("weird.bin", b"\x00\x01", odd_mime)},
        )
        # The MIME may be rejected at upload time depending on the
        # validator; if so, fall back to driving the same scenario by
        # having the parser fail on first run.
        if upload.status_code != 200:
            # Fallback: register a parser that fails the first call.
            class _FlakeyParser:
                name = "flakey"
                version = "test"
                supported_content_types = frozenset({PLAIN})
                _fail = True

                def parse(self, version, storage):  # noqa: ANN001
                    if _FlakeyParser._fail:
                        _FlakeyParser._fail = False
                        raise RuntimeError("simulated transient failure")
                    from app.schemas.extraction import (
                        RawExtraction,
                        RawSection,
                        SourceReference,
                    )

                    content = storage.get(version.storage_uri).decode("utf-8")
                    ref = SourceReference(
                        document_version_id=version.id,
                        section_id="s-0",
                        snippet=content[:24],
                    )
                    return RawExtraction(
                        document_version_id=version.id,
                        parser_name=self.name,
                        parser_version=self.version,
                        text=content,
                        sections=[
                            RawSection(
                                id="s-0",
                                heading="Body",
                                text=content,
                                source_reference_ids=[ref.id],
                            )
                        ],
                        source_references=[ref],
                    )

            services.parsers._by_content_type[PLAIN] = _FlakeyParser()  # type: ignore[attr-defined]
            version = _upload(client, body=b"retry me")
            # First attempt enqueues; worker fails it.
            kick = client.post(
                f"/documents/{version['document_id']}/versions/{version['id']}/extract",
            )
            assert kick.status_code == 202, kick.text
            _wait_for_status(
                client,
                document_id=version["document_id"],
                version_id=version["id"],
                target={DocumentVersionStatus.FAILED},
            )

            # Retry — the parser's flakey flag is now cleared.
            retry = client.post(
                f"/documents/{version['document_id']}/versions/{version['id']}/retry-extraction",
            )
            assert retry.status_code == 202, retry.text
            ExtractionJobSnapshot.model_validate(retry.json())
            assert retry.json()["status"] == (DocumentVersionStatus.QUEUED_FOR_EXTRACTION.value)
            final = _wait_for_status(
                client,
                document_id=version["document_id"],
                version_id=version["id"],
                target={DocumentVersionStatus.EXTRACTED},
            )
            assert final == DocumentVersionStatus.EXTRACTED.value
            return

        # Happy path with the odd MIME accepted.
        version = upload.json()
        kick = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/extract",
        )
        assert kick.status_code == 202, kick.text
        _wait_for_status(
            client,
            document_id=version["document_id"],
            version_id=version["id"],
            target={DocumentVersionStatus.FAILED},
        )

        # Widen the registry by registering a parser for the MIME on
        # the live services.
        class _LateParser:
            name = "late"
            version = "test"
            supported_content_types = frozenset({odd_mime})

            def parse(self, version, storage):  # noqa: ANN001
                from app.schemas.extraction import (
                    RawExtraction,
                    RawSection,
                    SourceReference,
                )

                content = storage.get(version.storage_uri)
                ref = SourceReference(
                    document_version_id=version.id,
                    section_id="s-0",
                    snippet=content[:24].decode("latin-1", errors="replace"),
                )
                return RawExtraction(
                    document_version_id=version.id,
                    parser_name=self.name,
                    parser_version=self.version,
                    text=content.decode("latin-1", errors="replace"),
                    sections=[
                        RawSection(
                            id="s-0",
                            heading="Body",
                            text=content.decode("latin-1", errors="replace") or "x",
                            source_reference_ids=[ref.id],
                        )
                    ],
                    source_references=[ref],
                )

        services.parsers._by_content_type[odd_mime] = _LateParser()  # type: ignore[attr-defined]

        retry = client.post(
            f"/documents/{version['document_id']}/versions/{version['id']}/retry-extraction",
        )
        assert retry.status_code == 202, retry.text
        ExtractionJobSnapshot.model_validate(retry.json())
        assert retry.json()["status"] == (DocumentVersionStatus.QUEUED_FOR_EXTRACTION.value)

        final = _wait_for_status(
            client,
            document_id=version["document_id"],
            version_id=version["id"],
            target={DocumentVersionStatus.EXTRACTED},
        )
        assert final == DocumentVersionStatus.EXTRACTED.value
