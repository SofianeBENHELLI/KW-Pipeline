"""HTTP coverage for ``GET /admin/hitl/state`` (EPIC-A close-out, #215).

Pins the contract the Admin HITL dashboard reads from:

- 200 with the full snapshot (config + buckets + pending count) when
  the scorer is enabled.
- 503 ``KW_HITL_DISABLED`` when ``KW_HITL_DISABLE_SCORER=true``.
- Bucket sorting by ``drift_ratio`` DESC.
- 403 when the caller is not admin.
- ``drift_ratio`` math (``samples_human_after_auto / max(samples_auto, 1)``).
- ``effective_sample_rate`` reflects the drift detector's ramp logic.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.validation_metadata import ValidationMetadata
from app.services.auth import encode_hs256
from app.services.sampling_state_store import SamplingBucket

# ADR-019 §2: production secret must be ≥ 32 bytes; tests mirror.
_SECRET = "k" * 32


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


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


def _seed_bucket(
    services,
    *,
    content_type: str,
    topic_cluster: str,
    auto: int = 0,
    human: int = 0,
    drift: int = 0,
) -> None:
    """Drive ``record_decision`` / ``record_drift_event`` enough times to
    land the bucket in the requested counter shape."""
    bucket = SamplingBucket(content_type=content_type, topic_cluster=topic_cluster)
    for _ in range(auto):
        services.sampling_state.record_decision(bucket=bucket, method="auto")
    for _ in range(human):
        services.sampling_state.record_decision(bucket=bucket, method="human")
    for _ in range(drift):
        services.sampling_state.record_drift_event(bucket=bucket)


# ─── 200 with the full snapshot ───────────────────────────────────────


class TestStateSnapshot:
    def test_empty_state_when_no_buckets(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True
        assert body["force_auto_corpus"] is False
        # Defaults from settings.py:
        assert body["threshold"] == pytest.approx(0.85)
        assert body["baseline_sample_rate"] == pytest.approx(0.05)
        assert body["drift_threshold"] == pytest.approx(0.10)
        assert body["drift_ramp_factor"] == pytest.approx(10.0)
        assert body["pending_auto_promotions"] == 0
        assert body["buckets"] == []

    def test_full_snapshot_with_seeded_buckets(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        _seed_bucket(
            services,
            content_type="text/plain",
            topic_cluster="compliance",
            auto=10,
            human=4,
            drift=2,  # drift_ratio = 0.20 → above 0.10 threshold
        )
        _seed_bucket(
            services,
            content_type="application/pdf",
            topic_cluster="finance",
            auto=20,
            human=2,
            drift=0,  # drift_ratio = 0.0 → at baseline
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["buckets"]) == 2
        # First bucket is the drifting one (sorted by drift_ratio DESC).
        first = body["buckets"][0]
        assert first["content_type"] == "text/plain"
        assert first["topic_cluster"] == "compliance"
        assert first["samples_taken"] == 14
        assert first["samples_auto"] == 10
        assert first["samples_human"] == 4
        assert first["samples_human_after_auto"] == 2
        assert first["drift_ratio"] == pytest.approx(0.20)
        # baseline 0.05 * 10x ramp = 0.5 (below the 1.0 cap).
        assert first["effective_sample_rate"] == pytest.approx(0.5)
        assert first["last_decision_at"] is not None

    def test_pending_count_reflects_validation_metadata(
        self,
        bearer_env: None,
    ) -> None:
        client, services = _client_and_services()
        # Drop a couple of pending auto rows directly into the
        # metadata store so we don't have to drive a full pipeline.
        services.validation_metadata.upsert(
            ValidationMetadata(
                version_id="ver-pending-1",
                routing_decision="auto",
                validation_method=None,
            )
        )
        services.validation_metadata.upsert(
            ValidationMetadata(
                version_id="ver-pending-2",
                routing_decision="auto",
                validation_method=None,
            )
        )
        # And one already-promoted row that should NOT count.
        services.validation_metadata.upsert(
            ValidationMetadata(
                version_id="ver-done",
                routing_decision="auto",
                validation_method="auto",
            )
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["pending_auto_promotions"] == 2


# ─── Bucket sorting ──────────────────────────────────────────────────


class TestBucketSorting:
    def test_buckets_sorted_by_drift_ratio_desc(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        # Three buckets with very different drift ratios:
        # A: 0.50 (5/10 drifts)
        _seed_bucket(
            services,
            content_type="text/plain",
            topic_cluster="A",
            auto=10,
            drift=5,
        )
        # B: 0.10 (1/10)
        _seed_bucket(
            services,
            content_type="text/plain",
            topic_cluster="B",
            auto=10,
            drift=1,
        )
        # C: 0.30 (3/10)
        _seed_bucket(
            services,
            content_type="text/plain",
            topic_cluster="C",
            auto=10,
            drift=3,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        clusters = [b["topic_cluster"] for b in response.json()["buckets"]]
        assert clusters == ["A", "C", "B"]


# ─── drift_ratio math ────────────────────────────────────────────────


class TestDriftRatioComputation:
    def test_drift_ratio_is_human_after_auto_over_auto(
        self,
        bearer_env: None,
    ) -> None:
        """ADR-023 §6: ratio = samples_human_after_auto / samples_auto."""
        client, services = _client_and_services()
        _seed_bucket(
            services,
            content_type="text/plain",
            topic_cluster="compliance",
            auto=10,
            drift=2,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        bucket = response.json()["buckets"][0]
        assert bucket["drift_ratio"] == pytest.approx(0.20)

    def test_cold_start_bucket_reports_zero_drift_and_baseline_rate(
        self,
        bearer_env: None,
    ) -> None:
        """``samples_auto == 0`` → drift_ratio coerced to 0.0; the
        detector returns the baseline rate."""
        client, services = _client_and_services()
        # All decisions human (samples_auto stays at 0).
        _seed_bucket(
            services,
            content_type="text/plain",
            topic_cluster="cold",
            human=5,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        bucket = response.json()["buckets"][0]
        assert bucket["samples_auto"] == 0
        assert bucket["drift_ratio"] == pytest.approx(0.0)
        assert bucket["effective_sample_rate"] == pytest.approx(0.05)


# ─── 403 / forbidden ────────────────────────────────────────────────


class TestForbiddenForNonAdmin:
    def test_reviewer_token_is_rejected(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "KW_FORBIDDEN"


# ─── 503 when scorer disabled ───────────────────────────────────────


class TestScorerDisabled:
    def test_returns_503_with_hitl_disabled(
        self,
        bearer_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KW_HITL_DISABLE_SCORER", "true")
        client, services = _client_and_services()
        # Tied kill switch: router unwired → state route 503s.
        assert services.hitl_router is None
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_HITL_DISABLED"
        assert "KW_HITL_DISABLE_SCORER" in body["error"]["message"]


# ─── force_auto_corpus surfaced ─────────────────────────────────────


class TestForceAutoCorpusFlag:
    def test_force_auto_flag_mirrors_env(
        self,
        bearer_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KW_HITL_FORCE_AUTO_CORPUS", "true")
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/hitl/state", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["force_auto_corpus"] is True
