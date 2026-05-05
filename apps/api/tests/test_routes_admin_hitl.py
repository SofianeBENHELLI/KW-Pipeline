"""HTTP coverage for ``POST /admin/hitl/run_auto_promote_pass`` (slice 3, #215).

Pins the route contract:

- 200 with empty result when nothing pending.
- 200 with promoted rows when scenario set up.
- 403 when caller is not admin.
- 422 on max_versions out-of-range (1..1000).
- 503 with ``KW_HITL_DISABLED`` when the worker is not wired
  (KW_HITL_DISABLE_SCORER=true).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.validation_metadata import ConfidenceScore, ValidationMetadata
from app.services.auth import encode_hs256
from app.services.confidence_scorer import ALL_SIGNALS

# ADR-019 §2: production secret must be ≥ 32 bytes; tests mirror.
_SECRET = "k" * 32


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch the app to bearer mode with a deterministic secret.

    ``KW_AUTH_DEV_USER`` is cleared so the dev-mode default (admin)
    doesn't shadow the bearer principal — without that we can't
    exercise the 403 path with a non-admin token.
    """
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _make_score(overall: float = 0.95) -> ConfidenceScore:
    return ConfidenceScore(
        overall=overall,
        signals=dict.fromkeys(ALL_SIGNALS, overall),
        weights=dict.fromkeys(ALL_SIGNALS, 0.2),
        ocr_override_active=False,
        computed_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        computed_by_version="v1",
    )


def _land_in_needs_review(services, *, filename="policy.txt", content=b"Hello world"):
    version = services.documents.upload(
        filename=filename, content_type="text/plain", content=content
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    return version.document_id, version.id


def _force_routing(services, *, version_id, score=None):
    """Persist routing_decision='auto' so the worker picks the row up."""
    existing = services.validation_metadata.get(version_id)
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            confidence_score=score or (existing and existing.confidence_score) or _make_score(),
            routing_decision="auto",
        )
    )


# ─── 200 with empty result ────────────────────────────────────────────


class TestEmptyPendingSet:
    def test_returns_empty_result_when_nothing_pending(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scanned"] == 0
        assert body["promoted"] == []
        assert body["skipped"] == []
        assert body["failed"] == []


# ─── 200 with promoted rows ───────────────────────────────────────────


class TestPromotionHappyPath:
    def test_promotes_pending_auto_row(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        document_id, version_id = _land_in_needs_review(services)
        _force_routing(services, version_id=version_id, score=_make_score(0.92))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scanned"] == 1
        assert len(body["promoted"]) == 1
        promoted = body["promoted"][0]
        assert promoted["document_id"] == document_id
        assert promoted["version_id"] == version_id
        assert promoted["score_overall"] == pytest.approx(0.92)
        assert body["skipped"] == []
        assert body["failed"] == []

        # FSM transitioned.
        refreshed = services.documents.get_version(document_id=document_id, version_id=version_id)
        assert refreshed.status == DocumentVersionStatus.VALIDATED


# ─── max_versions clamp ──────────────────────────────────────────────


class TestMaxVersionsClamp:
    def test_max_versions_caps_the_pass(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        for i in range(3):
            _, version_id = _land_in_needs_review(
                services,
                filename=f"file-{i}.txt",
                content=f"bytes {i}".encode(),
            )
            _force_routing(services, version_id=version_id)
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass?max_versions=2",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scanned"] == 2
        assert len(body["promoted"]) == 2

    def test_max_versions_zero_is_rejected(self, bearer_env: None) -> None:
        """``ge=1`` — 0 is below the floor; FastAPI rejects with 422."""
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass?max_versions=0",
            headers=headers,
        )

        assert response.status_code == 422

    def test_max_versions_above_ceiling_is_rejected(self, bearer_env: None) -> None:
        """``le=1000`` — 1001 is above the ceiling; FastAPI rejects with 422."""
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass?max_versions=1001",
            headers=headers,
        )

        assert response.status_code == 422

    def test_max_versions_one_is_accepted(self, bearer_env: None) -> None:
        """The boundary value lower bound — 1 should pass."""
        client, services = _client_and_services()
        for i in range(2):
            _, version_id = _land_in_needs_review(
                services,
                filename=f"file-{i}.txt",
                content=f"bytes {i}".encode(),
            )
            _force_routing(services, version_id=version_id)
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass?max_versions=1",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["scanned"] == 1


# ─── 403 on non-admin caller ─────────────────────────────────────────


class TestForbiddenForNonAdmin:
    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass",
            headers=headers,
        )

        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
        assert "reviewer" in body["error"]["message"]
        assert "admin" in body["error"]["message"]

    def test_viewer_token_is_rejected(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('viewer')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass",
            headers=headers,
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "KW_FORBIDDEN"


# ─── 503 when worker disabled ────────────────────────────────────────


class TestWorkerDisabled:
    def test_returns_503_with_hitl_disabled(
        self,
        bearer_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``KW_HITL_DISABLE_SCORER=true`` disables the scorer, the
        router, and (transitively) the auto-promoter. The route then
        503s with ``KW_HITL_DISABLED``."""
        monkeypatch.setenv("KW_HITL_DISABLE_SCORER", "true")
        client, services = _client_and_services()
        assert services.hitl_auto_promoter is None
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.post(
            "/admin/hitl/run_auto_promote_pass",
            headers=headers,
        )

        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_HITL_DISABLED"
        assert "KW_HITL_DISABLE_SCORER" in body["error"]["message"]
