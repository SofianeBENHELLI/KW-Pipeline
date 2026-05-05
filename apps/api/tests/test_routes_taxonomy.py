"""HTTP-level tests for ``GET /knowledge/taxonomy`` (ADR-017 / B2 / #249)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.knowledge import GraphNode


def _client_with_taxonomy(tmp_path, monkeypatch, yaml: str | None) -> TestClient:
    if yaml is not None:
        path = tmp_path / "taxonomy.yaml"
        path.write_text(yaml, encoding="utf-8")
        monkeypatch.setenv("KW_TAXONOMY_PATH", str(path))
    else:
        monkeypatch.delenv("KW_TAXONOMY_PATH", raising=False)
    services = build_services()
    return TestClient(create_app(services=services))


def _seed_topic_node(
    services_holder,
    *,
    topic_id: str,
    label: str,
    keywords: list[str] | None = None,
    summary: str | None = None,
) -> None:
    """Drop one ``kind="topic"`` node into the in-memory graph store.

    The hybrid taxonomy route (#249) reads these to synthesise the
    "computed" half of the response. We bypass the projector wiring
    here and write directly to the store — the topic-clustering
    pipeline already has its own integration tests.
    """
    services_holder.graph_store.upsert_nodes(
        [
            GraphNode(
                id=topic_id,
                kind="topic",
                label=label,
                properties={
                    "document_id": "doc-test",
                    "version_id": "ver-test",
                    "topic_id": topic_id,
                    "label": label,
                    "keywords": keywords or [],
                    "summary": summary,
                    "chunk_count": 0,
                    "chunk_ids": [],
                },
            )
        ]
    )


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
    # #249: every category carries a ``source`` flag. The YAML loader
    # tags everything as "imposed".
    for category in body["categories"]:
        assert category["source"] == "imposed"
    assert hr["subcategories"][0]["source"] == "imposed"


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


# ─── #249 hybrid-taxonomy tests ───────────────────────────────────────────


def test_route_merges_imposed_and_computed_with_imposed_winning(tmp_path, monkeypatch):
    """When YAML and topic-clustering both define ``id="hr"`` the
    operator's definition flows through and the computed entry is
    dropped — the operator override rule from ADR-017."""
    yaml = """
taxonomy:
  schema_version: v0.1
  categories:
    - id: hr
      label: People & HR
      description: Operator's HR description.
"""
    path = tmp_path / "taxonomy.yaml"
    path.write_text(yaml, encoding="utf-8")
    monkeypatch.setenv("KW_TAXONOMY_PATH", str(path))
    services = build_services()
    # Computed cluster collides with the imposed id.
    _seed_topic_node(
        services,
        topic_id="hr",
        label="Topic-derived HR",
        keywords=["hybrid", "remote"],
        summary="Auto-derived HR cluster.",
    )
    client = TestClient(create_app(services=services))
    response = client.get("/knowledge/taxonomy")

    assert response.status_code == 200
    body = response.json()
    assert body["is_configured"] is True
    # Single "hr" entry — the imposed one wins.
    hr_rows = [c for c in body["categories"] if c["id"] == "hr"]
    assert len(hr_rows) == 1
    hr = hr_rows[0]
    assert hr["source"] == "imposed"
    assert hr["label"] == "People & HR"
    assert hr["description"] == "Operator's HR description."


def test_route_returns_computed_only_when_no_imposed_yaml(tmp_path, monkeypatch):
    """No YAML configured but the topic-clustering path produced one
    or more clusters → the API still reports ``is_configured=true``
    and surfaces the computed categories."""
    monkeypatch.delenv("KW_TAXONOMY_PATH", raising=False)
    services = build_services()
    _seed_topic_node(
        services,
        topic_id="topic-cluster-42",
        label="Compliance memos",
        keywords=["gdpr", "compliance"],
        summary="Topic about compliance memos.",
    )
    _seed_topic_node(
        services,
        topic_id="topic-cluster-7",
        label="Hybrid work",
        keywords=["hybrid", "remote", "wfh"],
        summary=None,  # forces the keyword-based description fallback
    )
    client = TestClient(create_app(services=services))
    response = client.get("/knowledge/taxonomy")

    assert response.status_code == 200
    body = response.json()
    assert body["is_configured"] is True
    assert body["source_path"] is None
    ids = {c["id"] for c in body["categories"]}
    assert ids == {"topic-cluster-42", "topic-cluster-7"}
    for category in body["categories"]:
        assert category["source"] == "computed"
    # Empty-summary topic still has a non-empty description.
    fallback = next(c for c in body["categories"] if c["id"] == "topic-cluster-7")
    assert fallback["description"]
    assert "hybrid" in fallback["description"].lower()


def test_route_returns_empty_when_neither_imposed_nor_computed(tmp_path, monkeypatch):
    """Both halves empty → ``is_configured=false`` + ``categories=[]``.

    Mirrors the original "no YAML, no clusters" boot state.
    """
    monkeypatch.delenv("KW_TAXONOMY_PATH", raising=False)
    services = build_services()
    client = TestClient(create_app(services=services))
    response = client.get("/knowledge/taxonomy")

    assert response.status_code == 200
    body = response.json()
    assert body["is_configured"] is False
    assert body["categories"] == []
