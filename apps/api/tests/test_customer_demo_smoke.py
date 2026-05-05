import json
from pathlib import Path

from scripts.customer_demo_smoke import run_customer_demo


def test_customer_demo_smoke_runs_full_review_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
    artifact_dir = tmp_path / "artifacts"

    summary = run_customer_demo(
        data_dir=tmp_path / "data",
        artifact_dir=artifact_dir,
        reset=True,
        emit=None,
    )

    assert len(summary["processed_versions"]) == 5
    assert {item["parser_name"] for item in summary["processed_versions"]} == {
        "docx",
        "plain_text",
    }
    assert all(
        item["preview_validation_status"] == "needs_review"
        for item in summary["processed_versions"]
    )
    assert all(item["review_status"] == "VALIDATED" for item in summary["processed_versions"])
    assert all(
        item["semantic_validation_status"] == "validated" for item in summary["processed_versions"]
    )

    duplicate = summary["duplicate"]
    assert duplicate["status"] == "DUPLICATE_DETECTED"
    assert duplicate["extract_status_code"] == 409

    catalog = json.loads((artifact_dir / "catalog.json").read_text(encoding="utf-8"))
    # Four distinct-bytes families (supplier policy, quality handbook,
    # success brief, contract memo). The supplier policy family carries
    # three versions: v1, the explicit-document_id v2, and the
    # archived-name duplicate (issue #59 — anonymous duplicate uploads
    # now stitch into the original family).
    assert len(catalog["items"]) == 4
    assert any(len(document["versions"]) == 3 for document in catalog["items"])

    markdown_paths = sorted((artifact_dir / "markdown").glob("*.md"))
    assert len(markdown_paths) == 5
    assert all("## Source Lineage" in path.read_text(encoding="utf-8") for path in markdown_paths)
    assert all(Path(item["markdown_artifact"]).exists() for item in summary["processed_versions"])


def test_customer_demo_smoke_writes_graph_artifacts_and_stats(tmp_path, monkeypatch):
    """Demo KG (#145, #146): every validated version writes a graph
    artifact, ``run_summary.json`` carries aggregate graph stats, and
    the hero fixture produces the chunk + topic + relation richness
    the demo promises.

    Deterministic: no Anthropic, no Neo4j — the v0.2 projection is
    fully local.
    """
    monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    artifact_dir = tmp_path / "artifacts"

    summary = run_customer_demo(
        data_dir=tmp_path / "data",
        artifact_dir=artifact_dir,
        reset=True,
        emit=None,
    )

    # One graph artifact per validated version.
    graph_paths = sorted((artifact_dir / "graph").glob("*.json"))
    assert len(graph_paths) == len(summary["processed_versions"]), (
        "Each validated version should write exactly one graph artifact."
    )
    for entry in summary["processed_versions"]:
        graph_path = Path(entry["graph_artifact"])
        assert graph_path.exists(), f"Missing graph artifact for {entry['key']}"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        assert {n["kind"] for n in graph["nodes"]} >= {"document", "version", "chunk"}, (
            f"{entry['key']} graph is missing core node kinds — projection regressed?"
        )

    # Aggregate stats land on ``run_summary.json``.
    aggregate = summary["graph"]
    assert {"node_count", "edge_count", "chunk_count", "topic_count", "relation_count"} <= (
        aggregate.keys()
    )
    assert aggregate["chunk_count"] >= 1, "Expected chunk nodes across the demo run."

    # Hero fixture richness — #147 acceptance, asserted via the v0.2
    # projection contract.
    hero = next(e for e in summary["processed_versions"] if e["key"] == "quality_program_handbook")
    hero_graph = json.loads(Path(hero["graph_artifact"]).read_text(encoding="utf-8"))
    hero_kinds = [n["kind"] for n in hero_graph["nodes"]]
    chunk_count = hero_kinds.count("chunk")
    topic_count = hero_kinds.count("topic")
    relation_edges = [
        e
        for e in hero_graph["edges"]
        if e["kind"] in {"related_to", "shares_keyword", "same_topic_as"}
    ]

    assert 8 <= chunk_count <= 15, (
        f"Hero fixture should produce 8-15 chunks (#147), got {chunk_count}."
    )
    assert topic_count >= 3, f"Hero fixture should produce ≥ 3 topics (#147), got {topic_count}."
    assert len(relation_edges) >= 8, (
        f"Hero fixture should produce ≥ 8 chunk-relation edges (#147), got {len(relation_edges)}."
    )

    # Audit-trail contract from
    # ``docs/architecture/knowledge_graph_payload.md`` — every
    # deterministic chunk-relation edge carries non-empty
    # ``shared_keywords``, ``reason``, and a score in [0, 1].
    for edge in relation_edges:
        props = edge["properties"]
        assert props.get("reason"), f"relation {edge['id']} has empty reason"
        assert props.get("shared_keywords"), f"relation {edge['id']} has empty shared_keywords"
        assert 0.0 <= float(props["score"]) <= 1.0
