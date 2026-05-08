"""HTTP coverage for the multi-kind Explorer search route (#313).

503 when Phase-3 vector search is disabled, 422 on invalid query
params, 200 + grouped response otherwise. Scope filtering uses the
same per-document caching pattern as ``GET /knowledge/search``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


# ─── 503 when Phase 3 is off ──────────────────────────────────────────


class TestPhase3Disabled:
    def test_returns_503_with_disabled_envelope(self, monkeypatch) -> None:
        # Default test build has no VOYAGE_API_KEY, so
        # ``knowledge_explore_search`` is None and the route 503s.
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("KW_VOYAGE_API_KEY", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/explore/search", params={"q": "hello"})
        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_VECTOR_SEARCH_DISABLED"
        assert body["error"]["remediation"]


# ─── 422 on validation ───────────────────────────────────────────────


class TestQueryValidation:
    def test_missing_query_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get("/knowledge/explore/search")
        assert response.status_code == 422

    def test_chunk_limit_above_ceiling_returns_422(self, monkeypatch) -> None:
        monkeypatch.delenv("KW_AUTH_MODE", raising=False)
        client, _ = _client_and_services()
        response = client.get(
            "/knowledge/explore/search",
            params={"q": "hello", "chunk_limit": 1000},
        )
        assert response.status_code == 422
