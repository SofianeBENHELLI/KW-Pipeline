"""Tests for ``POST /admin/reconcile`` and ``extraction.queue_depth`` (#40, ADR-006 §5).

Two surfaces land in the same file because they're the only two genuine
deltas from the 2026-05-14 progress plan's #40 follow-up scope — the
plan's ``extraction.retry`` and ``extraction.dead_letter`` events
already have functional equivalents on main (``extraction.retried`` on
the retry-extraction route, ``extraction.recovery.summary`` at the end
of the boot-time scan).

Coverage:

- ``POST /admin/reconcile`` recovers stuck versions when called at
  runtime (parity with the lifespan boot scan).
- The route short-circuits when ``KW_EXTRACTION_INLINE=true`` and
  reports ``skipped_inline=true`` so operators see the no-op clearly.
- The route is 403 for non-admin callers (ADR-019 §3, same gate as the
  other ``/admin/*`` routes).
- A returning-zero call still 200s with ``recovered_count=0``.
- The route always emits ``admin.reconcile.invoked`` with the caller's
  actor for the audit feed.
- ``extraction.queue_depth`` fires once per successful enqueue with
  ``qsize`` / ``maxsize`` / ``is_full`` — the operator-facing gauge.
"""

from __future__ import annotations

import logging
import threading

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.services.auth import encode_hs256

# ADR-019 §2: production secret must be ≥ 32 bytes; tests mirror.
_SECRET = "k" * 32
PLAIN = "text/plain"


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _services_with(*, extraction_inline: bool, queue_size: int = 4, workers: int = 1):
    services = build_services()
    object.__setattr__(services.settings, "extraction_inline", extraction_inline)
    object.__setattr__(services.settings, "extraction_queue_size", queue_size)
    object.__setattr__(services.settings, "extraction_workers", workers)
    return services


def _stick_version_in_extracting(services, *, filename: str = "stuck.txt") -> tuple[str, str]:
    """Upload + flip the version to ``EXTRACTING`` so the reconcile route
    has a row to act on. Mirrors the helper in
    ``test_extraction_recovery.py``."""
    body = f"content for {filename}".encode()
    version = services.documents.upload(filename, PLAIN, body)
    services.documents.update_status(
        version.document_id, version.id, DocumentVersionStatus.EXTRACTING
    )
    return version.document_id, version.id


# ─── /admin/reconcile — happy path ────────────────────────────────────


class TestReconcileHappyPath:
    def test_recovers_a_stuck_version(self, bearer_env: None) -> None:
        services = _services_with(extraction_inline=False)
        document_id, version_id = _stick_version_in_extracting(services)
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post("/admin/reconcile", headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {"recovered_count": 1, "skipped_inline": False}
        # The stuck version is now FAILED with the canonical reason.
        version = services.documents.get_version(document_id, version_id)
        assert version.status == DocumentVersionStatus.FAILED
        assert version.failure_reason is not None
        assert "process restart" in version.failure_reason.lower()

    def test_recovers_multiple_versions(self, bearer_env: None) -> None:
        services = _services_with(extraction_inline=False)
        targets = [
            _stick_version_in_extracting(services, filename=f"stuck-{i}.txt") for i in range(3)
        ]
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post("/admin/reconcile", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["recovered_count"] == 3
        for document_id, version_id in targets:
            assert (
                services.documents.get_version(document_id, version_id).status
                == DocumentVersionStatus.FAILED
            )

    def test_returns_zero_when_no_versions_are_stuck(self, bearer_env: None) -> None:
        services = _services_with(extraction_inline=False)
        # Upload but don't flip — STORED is not a stuck state.
        services.documents.upload("clean.txt", PLAIN, b"clean")
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post("/admin/reconcile", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"recovered_count": 0, "skipped_inline": False}


# ─── /admin/reconcile — inline mode short-circuit ─────────────────────


class TestReconcileInlineMode:
    def test_inline_mode_short_circuits_without_touching_stuck_rows(self, bearer_env: None) -> None:
        services = _services_with(extraction_inline=True)
        # Force a stuck-looking row (in inline mode this can't happen
        # organically — we set it up to prove the route doesn't touch
        # it when the flag is on).
        document_id, version_id = _stick_version_in_extracting(services)
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post("/admin/reconcile", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"recovered_count": 0, "skipped_inline": True}
        # The version stayed EXTRACTING — the inline short-circuit is
        # observable: zero recovered + the row is unchanged.
        assert (
            services.documents.get_version(document_id, version_id).status
            == DocumentVersionStatus.EXTRACTING
        )


# ─── /admin/reconcile — auth ──────────────────────────────────────────


class TestReconcileForbiddenForNonAdmin:
    def test_viewer_token_is_rejected(self, bearer_env: None) -> None:
        services = _services_with(extraction_inline=False)
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('viewer')}"}

        response = client.post("/admin/reconcile", headers=headers)

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "KW_FORBIDDEN"

    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        services = _services_with(extraction_inline=False)
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.post("/admin/reconcile", headers=headers)

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "KW_FORBIDDEN"


# ─── /admin/reconcile — audit trail ───────────────────────────────────


class TestReconcileAuditTrail:
    def test_emits_invoked_event_with_actor(
        self,
        bearer_env: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        services = _services_with(extraction_inline=False)
        client = TestClient(create_app(services=services))
        headers = {"Authorization": f"Bearer {_token('admin', user_id='ada')}"}

        caplog.set_level(logging.INFO, logger="app.routes.admin")
        response = client.post("/admin/reconcile", headers=headers)
        assert response.status_code == 200

        events = [r for r in caplog.records if r.message == "admin.reconcile.invoked"]
        assert events, "admin.reconcile.invoked should fire exactly once per call"
        event = events[-1]
        assert getattr(event, "actor", None) == "ada"
        assert getattr(event, "actor_role", None) == "admin"
        assert getattr(event, "inline_mode", None) is False


# ─── extraction.queue_depth — emitted on every successful enqueue ─────


class TestQueueDepthLogEvent:
    def test_fires_once_per_enqueue_with_gauge_fields(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The route emits ``extraction.queue_depth`` with ``qsize`` /
        ``maxsize`` / ``is_full`` after a successful put. Operators pipe
        that into Prometheus via a log-to-metric exporter for the
        backpressure gauge."""
        services = _services_with(extraction_inline=False, queue_size=8, workers=1)
        # Stall the worker so the queued job stays observable.
        block = threading.Event()

        class _BlockingParser:
            name = "blocking_plain"
            version = "test"
            supported_content_types = frozenset({PLAIN})

            def parse(self, version, storage):  # noqa: ANN001
                from app.schemas.extraction import (
                    RawExtraction,
                    RawSection,
                    SourceReference,
                )

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
            caplog.set_level(logging.INFO, logger="app.routes.lifecycle")
            with TestClient(app) as client:
                # Upload + extract — async mode, queue-depth gauge fires
                # in ``_put_and_build_snapshot``.
                response = client.post(
                    "/documents/upload",
                    files={"file": ("note.txt", b"hello world", PLAIN)},
                )
                assert response.status_code == 200, response.text
                version = response.json()
                extract = client.post(
                    f"/documents/{version['document_id']}/versions/{version['id']}/extract",
                )
                assert extract.status_code == 202, extract.text

                gauge_events = [r for r in caplog.records if r.message == "extraction.queue_depth"]
                assert len(gauge_events) == 1, (
                    "extraction.queue_depth should fire exactly once per enqueue"
                )
                event = gauge_events[0]
                assert getattr(event, "qsize", None) == 1
                assert getattr(event, "maxsize", None) == 8
                assert getattr(event, "is_full", None) is False
                assert getattr(event, "document_id", None) == version["document_id"]
                assert getattr(event, "version_id", None) == version["id"]
        finally:
            block.set()
