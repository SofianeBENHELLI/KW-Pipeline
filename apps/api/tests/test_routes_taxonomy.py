"""HTTP-level tests for ``GET /knowledge/taxonomy`` (ADR-017 / B2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app


def _client_with_taxonomy(tmp_path, monkeypatch, yaml: str | None) -> TestClient:
    if yaml is not None:
        path = tmp_path / "taxonomy.yaml"
        path.write_text(yaml, encoding="utf-8")
        monkeypatch.setenv("KW_TAXONOMY_PATH", str(path))
    else:
        monkeypatch.delenv("KW_TAXONOMY_PATH", raising=False)
    services = build_services()
    return TestClient(create_app(services=services))


def test_route_returns_not_configured_when_no_path(tmp_path, monkeypatch):
    client = _client_with_taxonomy(tmp_path, monkeypatch, yaml=None)
    response = client.get("/knowledge/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert body["is_configured"] is False
    assert body["categories"] == []
    assert body["source_path"] is None
    assert body["schema_version"] == "v0.1"


def test_route_returns_configured_when_yaml_loaded(tmp_path, monkeypatch):
    yaml = """
taxonomy:
  schema_version: v0.1
  categories:
    - id: hr
      label: People & HR
      description: Personnel policies and onboarding.
      subcategories:
        - id: hr.hybrid_work
          label: Hybrid work
          description: On-site / remote / cross-border.
    - id: legal
      label: Legal & Risk
      description: Compliance and contracts.
"""
    client = _client_with_taxonomy(tmp_path, monkeypatch, yaml=yaml)
    response = client.get("/knowledge/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert body["is_configured"] is True
    assert body["source_path"].endswith("taxonomy.yaml")
    assert {c["id"] for c in body["categories"]} == {"hr", "legal"}
    hr = next(c for c in body["categories"] if c["id"] == "hr")
    assert len(hr["subcategories"]) == 1
    assert hr["subcategories"][0]["id"] == "hr.hybrid_work"


def test_route_returns_not_configured_when_path_points_at_missing_file(tmp_path, monkeypatch):
    """A stale ``KW_TAXONOMY_PATH`` boots the app rather than crashing."""
    monkeypatch.setenv("KW_TAXONOMY_PATH", str(tmp_path / "nope.yaml"))
    services = build_services()
    client = TestClient(create_app(services=services))
    response = client.get("/knowledge/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert body["is_configured"] is False
    assert body["categories"] == []
