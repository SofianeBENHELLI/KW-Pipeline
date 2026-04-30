"""Route-level guardrails for `POST /documents/upload`.

Covers issue #37: oversized uploads return 413, disallowed content types
return 415, env vars are read at request time so each test can configure
them independently.

Also covers issue #41: upload streaming bounds peak memory and the 413
short-circuits before the full body is materialised.
"""

import tracemalloc

from fastapi.testclient import TestClient

from app.main import create_app


def _client():
    return TestClient(create_app())


class TestUploadSizeLimit:
    def test_oversized_upload_returns_413(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
        client = _client()

        # 11 bytes — one over the 10-byte ceiling.
        response = client.post(
            "/documents/upload",
            files={"file": ("big.txt", b"01234567890", "text/plain")},
        )

        assert response.status_code == 413
        assert response.json()["detail"] == "Upload exceeds limit of 10 bytes"

    def test_within_limit_is_accepted(self, monkeypatch):
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "32")
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("ok.txt", b"hello world", "text/plain")},
        )

        assert response.status_code == 200

    def test_default_limit_applies_when_env_unset(self, monkeypatch):
        """Without MAX_UPLOAD_BYTES the default 50 MiB is enforced — a tiny
        upload sails through."""
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("small.txt", b"hi", "text/plain")},
        )

        assert response.status_code == 200


class TestContentTypeAllowlist:
    def test_disallowed_content_type_returns_415(self):
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("evil.bin", b"payload", "application/octet-stream")},
        )

        assert response.status_code == 415
        assert response.json()["detail"] == (
            "Content type 'application/octet-stream' is not allowed. Allowed: text/plain"
        )

    def test_allowlist_accepts_parameterised_content_type(self):
        """`text/plain; charset=utf-8` must be accepted when `text/plain` is
        on the allowlist — RFC 7231 lets clients append parameters and we
        gate on the bare media type."""
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("note.txt", b"hello", "text/plain; charset=utf-8")},
        )

        assert response.status_code == 200

    def test_custom_allowlist_respected(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_CONTENT_TYPES", "application/json,text/plain")
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("data.json", b"{}", "application/json")},
        )

        assert response.status_code == 200

    def test_custom_allowlist_lists_sorted_types_in_error(self, monkeypatch):
        """When multiple types are allowed, the 415 detail lists them sorted
        and comma-joined for stable error messages."""
        monkeypatch.setenv("ALLOWED_CONTENT_TYPES", "text/plain,application/json")
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("evil.bin", b"x", "application/octet-stream")},
        )

        assert response.status_code == 415
        assert response.json()["detail"] == (
            "Content type 'application/octet-stream' is not allowed. "
            "Allowed: application/json, text/plain"
        )

    def test_default_allowlist_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
        client = _client()

        # text/plain (default) → accepted.
        ok = client.post(
            "/documents/upload",
            files={"file": ("a.txt", b"bytes", "text/plain")},
        )
        assert ok.status_code == 200

        # text/html → rejected by the default allowlist.
        nope = client.post(
            "/documents/upload",
            files={"file": ("a.html", b"<p/>", "text/html")},
        )
        assert nope.status_code == 415


class _ChunkedUpload:
    """Minimal `UploadFile`-shaped stand-in that yields its body in fixed-size
    chunks from a backing :class:`SpooledTemporaryFile`.

    The TestClient buffers the entire multipart request body upfront before
    sending — that buffering is observable to ``tracemalloc`` and would
    drown out any signal from the route itself. To measure the route's own
    streaming behaviour we bypass the test client and feed the handler an
    UploadFile-shaped object directly, so the only allocations attributed
    to the route are the ones the route makes.
    """

    def __init__(self, filename: str, content_type: str, source) -> None:
        self.filename = filename
        self.content_type = content_type
        self._source = source

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._source.read()
        return self._source.read(size)


def _build_filesystem_router(tmp_path):
    """Wire the upload route against a filesystem-backed storage service.

    Streaming pays off when the storage backend can write incrementally —
    the in-memory backend has to accumulate bytes by definition, so we use
    ``FileSystemStorageService`` to get a true streaming-to-disk path and
    a meaningful peak-RSS bound on the route.
    """
    from app.dependencies import PipelineServices
    from app.routes import build_router
    from app.services.document_parser import PlainTextParser
    from app.services.document_service import DocumentService
    from app.services.extraction_job_service import ExtractionJobService
    from app.services.markdown_generator import MarkdownGenerator
    from app.services.semantic_extractor import SemanticExtractor
    from app.services.semantic_output_service import SemanticOutputService
    from app.services.storage_service import FileSystemStorageService

    storage = FileSystemStorageService(root=tmp_path / "raw")
    documents = DocumentService(storage=storage)
    parser = PlainTextParser()
    extraction_jobs = ExtractionJobService(documents=documents, parser=parser)
    semantic_extractor = SemanticExtractor()
    markdown_generator = MarkdownGenerator()
    services = PipelineServices(
        storage=storage,
        documents=documents,
        parser=parser,
        extraction_jobs=extraction_jobs,
        semantic_extractor=semantic_extractor,
        markdown_generator=markdown_generator,
        semantic_outputs=SemanticOutputService(
            documents=documents,
            extraction_jobs=extraction_jobs,
            semantic_extractor=semantic_extractor,
            markdown_generator=markdown_generator,
        ),
    )
    router = build_router(services)
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/documents/upload"
    )


def _call_upload_route_directly(
    payload: bytes,
    upload_handler,
    *,
    filename="big.txt",
    content_type="text/plain",
):
    """Invoke ``upload_document`` synchronously without spinning up a client.

    Returns a tuple of ``(result, peak_bytes)`` where ``peak_bytes`` is the
    tracemalloc peak captured strictly across the route invocation.
    """
    import asyncio
    import tempfile

    with tempfile.SpooledTemporaryFile(max_size=64 * 1024, mode="w+b") as backing:
        backing.write(payload)
        backing.seek(0)
        upload = _ChunkedUpload(filename, content_type, backing)

        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            result = asyncio.run(upload_handler(file=upload, document_id=None))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    return result, peak


class TestUploadStreaming:
    """Behaviour added by issue #41 — peak memory must track the chunk size,
    not the payload size, and the 413 short-circuits early.

    These tests bypass ``TestClient`` to isolate the route's allocations.
    """

    def test_5mib_upload_peak_allocation_bounded(self, monkeypatch, tmp_path):
        """Upload 5 MiB through the route handler while tracing allocations.

        With the streaming pipeline (≤8 MiB chunks → spooled temp file →
        filesystem storage written chunk-by-chunk) the per-request peak
        tracks the chunk size, not the payload-times-multiple of the old
        ``await file.read()`` plus the bytes-based service path that
        copied the full payload through ``DocumentService._build_version``.

        The 16 MiB ceiling here is set so the previous implementation
        (full body in memory + copy through ``compute_sha256(content)`` +
        copy through ``storage.put(content)``) would breach it, while the
        streaming path comfortably stays below.
        """
        monkeypatch.setenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
        payload = b"x" * (5 * 1024 * 1024)
        upload_handler = _build_filesystem_router(tmp_path)

        result, peak = _call_upload_route_directly(payload, upload_handler)

        assert result.file_size == len(payload)
        assert peak < 16 * 1024 * 1024, f"streaming regressed: peak={peak} bytes"

    def test_oversized_upload_413_short_circuits_before_full_buffer(self, monkeypatch, tmp_path):
        """A body larger than the ceiling must 413 without materialising the
        full payload — peak allocation stays under the body size."""
        import asyncio
        import tempfile

        from fastapi import HTTPException

        monkeypatch.setenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
        payload = b"y" * (51 * 1024 * 1024)
        upload_handler = _build_filesystem_router(tmp_path)

        with tempfile.SpooledTemporaryFile(max_size=64 * 1024, mode="w+b") as backing:
            backing.write(payload)
            backing.seek(0)
            upload = _ChunkedUpload("oversize.txt", "text/plain", backing)

            tracemalloc.start()
            try:
                tracemalloc.reset_peak()
                try:
                    asyncio.run(upload_handler(file=upload, document_id=None))
                except HTTPException as exc:
                    assert exc.status_code == 413
                else:
                    raise AssertionError("Expected HTTPException(413).")
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

        # If the route had buffered the whole upload before checking, peak
        # would approach the 51 MiB body size. Streaming caps it at one
        # chunk plus framing overhead — well under the body itself.
        assert peak < 20 * 1024 * 1024, f"413 did not short-circuit: peak={peak} bytes"

    def test_streamed_upload_records_correct_size_and_hash(self, monkeypatch):
        """End-to-end check via ``TestClient`` that the streamed pipeline
        produces the right digest and byte count for a non-trivial payload."""
        from hashlib import sha256

        monkeypatch.setenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
        client = _client()
        payload = (b"abcdef0123" * 1024) * 100

        response = client.post(
            "/documents/upload",
            files={"file": ("p.txt", payload, "text/plain")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["file_size"] == len(payload)
        assert body["sha256"] == sha256(payload).hexdigest()

    def test_streamed_upload_empty_body_returns_400(self, monkeypatch):
        """The streaming path must keep the existing 400-on-empty contract."""
        monkeypatch.setenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
        client = _client()

        response = client.post(
            "/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Uploaded file is empty."
