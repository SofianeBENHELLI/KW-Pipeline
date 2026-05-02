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
