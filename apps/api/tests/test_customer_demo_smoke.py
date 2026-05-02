import json
from pathlib import Path

from scripts.customer_demo_smoke import build_parser, run_customer_demo


def test_customer_demo_smoke_runs_full_review_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
    artifact_dir = tmp_path / "artifacts"

    summary = run_customer_demo(
        data_dir=tmp_path / "data",
        artifact_dir=artifact_dir,
        reset=True,
        emit=None,
    )

    assert len(summary["processed_versions"]) == 4
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
    assert len(catalog["items"]) == 4
    assert any(len(document["versions"]) == 2 for document in catalog["items"])

    markdown_paths = sorted((artifact_dir / "markdown").glob("*.md"))
    assert len(markdown_paths) == 4
    assert all("## Source Lineage" in path.read_text(encoding="utf-8") for path in markdown_paths)
    assert all(Path(item["markdown_artifact"]).exists() for item in summary["processed_versions"])


def test_graph_out_flag_is_registered_and_optional():
    """The --graph-out flag is wired and defaults to None.

    The CLI surface is the contract that downstream automation (CI, the
    customer-demo Make target) drives, so guard the flag's existence and
    its optional-by-default behaviour. Issue #145 (partial).
    """
    parser = build_parser()
    namespace = parser.parse_args([])
    assert namespace.graph_out is None

    namespace = parser.parse_args(["--graph-out", "/tmp/graph.json"])
    assert namespace.graph_out == Path("/tmp/graph.json")


def test_graph_out_skips_gracefully_when_knowledge_layer_disabled(tmp_path, monkeypatch):
    """With KW_KNOWLEDGE_LAYER_ENABLED=false the runner logs and skips.

    Issue #145 (partial). The runner must remain runnable in the default
    configuration — no Neo4j, no Anthropic key — so passing ``--graph-out``
    in that mode should *not* fail the run, just emit a clear log line and
    leave the artifact path untouched. This is the contract used by the
    PR-checks smoke target until issue #144 lands the rich payload.
    """
    monkeypatch.delenv("ALLOWED_CONTENT_TYPES", raising=False)
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "false")
    artifact_dir = tmp_path / "artifacts"
    graph_out = tmp_path / "graph.json"
    messages: list[str] = []

    summary = run_customer_demo(
        data_dir=tmp_path / "data",
        artifact_dir=artifact_dir,
        reset=True,
        graph_out=graph_out,
        emit=messages.append,
    )

    assert "graph_export" in summary, (
        "Runner should record graph-export status when --graph-out is set."
    )
    export = summary["graph_export"]
    assert export["skipped"] is True
    assert export["reason"] == "knowledge_layer_disabled"
    assert export["path"] == str(graph_out)
    assert not graph_out.exists(), (
        "When the knowledge layer is disabled the runner must not write a stub artifact."
    )
    assert any("knowledge layer disabled" in message for message in messages), (
        f"Expected a 'knowledge layer disabled' log line, got: {messages!r}"
    )
