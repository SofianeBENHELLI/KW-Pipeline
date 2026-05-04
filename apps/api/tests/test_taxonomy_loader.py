"""Tests for the operator-imposed taxonomy loader (ADR-017 §3 + §5).

The loader reads YAML from disk and returns a parsed
:class:`Taxonomy` plus the absolute path it loaded from. The
default suite never reads from a real ``KW_TAXONOMY_PATH``; every
test writes its own YAML into ``tmp_path`` so tests are
hermetic and parallel-safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.taxonomy import (
    MAX_TAXONOMY_DEPTH,
    MAX_TAXONOMY_FANOUT,
    TAXONOMY_SCHEMA_VERSION,
)
from app.services.taxonomy_loader import TaxonomyLoadError, load_taxonomy

# ─── Helpers ─────────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ─── No-path / missing-file paths ────────────────────────────────────────


def test_load_returns_none_for_none_path():
    taxonomy, source = load_taxonomy(None)
    assert taxonomy is None
    assert source is None


def test_load_returns_none_for_empty_string_path():
    taxonomy, source = load_taxonomy("")
    assert taxonomy is None
    assert source is None


def test_load_returns_none_for_whitespace_path():
    taxonomy, source = load_taxonomy("   ")
    assert taxonomy is None
    assert source is None


def test_load_returns_none_when_file_missing(tmp_path):
    """A configured path that doesn't exist must not crash startup."""
    missing = tmp_path / "nope.yaml"
    taxonomy, source = load_taxonomy(missing)
    assert taxonomy is None
    # Source is the resolved path so operators can grep their logs.
    assert source == missing.resolve()


def test_load_returns_empty_for_empty_file(tmp_path):
    """An empty YAML file is a valid (yet empty) taxonomy."""
    p = _write(tmp_path / "tax.yaml", "")
    taxonomy, source = load_taxonomy(p)
    assert taxonomy is not None
    assert taxonomy.categories == []
    assert source == p.resolve()


# ─── Happy path ───────────────────────────────────────────────────────────


VALID_YAML = """
taxonomy:
  schema_version: v0.1
  categories:
    - id: hr
      label: People & HR
      description: Personnel policies and onboarding documents.
      subcategories:
        - id: hr.hybrid_work
          label: Hybrid work
          description: Documents about on-site / remote / cross-border work.
    - id: legal
      label: Legal & Risk
      description: Contracts, compliance, and regulatory documents.
"""


def test_load_parses_valid_taxonomy(tmp_path):
    p = _write(tmp_path / "tax.yaml", VALID_YAML)
    taxonomy, _ = load_taxonomy(p)
    assert taxonomy is not None
    assert taxonomy.schema_version == TAXONOMY_SCHEMA_VERSION
    assert {c.id for c in taxonomy.categories} == {"hr", "legal"}
    hr = next(c for c in taxonomy.categories if c.id == "hr")
    assert len(hr.subcategories) == 1
    assert hr.subcategories[0].id == "hr.hybrid_work"


def test_load_accepts_flat_root_without_taxonomy_key(tmp_path):
    """The loader tolerates documents without the ``taxonomy:`` wrapper."""
    flat = """
schema_version: v0.1
categories:
  - id: a
    label: Alpha
    description: Alpha category.
"""
    p = _write(tmp_path / "tax.yaml", flat)
    taxonomy, _ = load_taxonomy(p)
    assert taxonomy is not None
    assert taxonomy.categories[0].id == "a"


# ─── Validation failures ─────────────────────────────────────────────────


def test_invalid_yaml_raises_with_path(tmp_path):
    p = _write(tmp_path / "tax.yaml", "this is: not: valid: yaml: [")
    with pytest.raises(TaxonomyLoadError, match="not valid YAML"):
        load_taxonomy(p)


def test_unsupported_schema_version_raises(tmp_path):
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  schema_version: v0.99\n  categories: []\n",
    )
    with pytest.raises(TaxonomyLoadError, match="schema_version"):
        load_taxonomy(p)


def test_missing_id_raises(tmp_path):
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories:\n    - label: x\n      description: y\n",
    )
    with pytest.raises(TaxonomyLoadError, match="missing a string `id`"):
        load_taxonomy(p)


def test_invalid_id_format_raises(tmp_path):
    """ids must be lower-snake (with dot/dash). Uppercase rejected."""
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories:\n    - id: HR\n      label: x\n      description: y\n",
    )
    with pytest.raises(TaxonomyLoadError, match="must match"):
        load_taxonomy(p)


def test_missing_description_raises(tmp_path):
    """The classifier reads `description` — it MUST be present."""
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories:\n    - id: hr\n      label: People\n",
    )
    with pytest.raises(TaxonomyLoadError, match="description"):
        load_taxonomy(p)


def test_duplicate_id_across_tree_raises(tmp_path):
    yaml = """
taxonomy:
  categories:
    - id: hr
      label: A
      description: A.
      subcategories:
        - id: hr
          label: B
          description: B.
"""
    p = _write(tmp_path / "tax.yaml", yaml)
    with pytest.raises(TaxonomyLoadError, match="duplicate category id"):
        load_taxonomy(p)


def test_excessive_nesting_depth_raises(tmp_path):
    """``MAX_TAXONOMY_DEPTH`` is enforced at load time."""
    # Build a chain of depth MAX_TAXONOMY_DEPTH + 1.
    lines = ["taxonomy:", "  categories:"]
    indent = "    "
    for i in range(MAX_TAXONOMY_DEPTH + 1):
        lines.append(f"{indent}- id: c{i}")
        lines.append(f"{indent}  label: c{i}")
        lines.append(f"{indent}  description: c{i}.")
        lines.append(f"{indent}  subcategories:")
        indent += "    "
    yaml = "\n".join(lines) + "\n"
    p = _write(tmp_path / "tax.yaml", yaml)
    with pytest.raises(TaxonomyLoadError, match="maximum nesting depth"):
        load_taxonomy(p)


def test_excessive_fanout_raises(tmp_path):
    items = "\n".join(
        f"    - id: c{i}\n      label: c{i}\n      description: c{i}."
        for i in range(MAX_TAXONOMY_FANOUT + 1)
    )
    yaml = f"taxonomy:\n  categories:\n{items}\n"
    p = _write(tmp_path / "tax.yaml", yaml)
    with pytest.raises(TaxonomyLoadError, match="must be <="):
        load_taxonomy(p)


def test_categories_not_a_list_raises(tmp_path):
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories: not-a-list\n",
    )
    with pytest.raises(TaxonomyLoadError, match="must be a list"):
        load_taxonomy(p)


def test_root_not_a_mapping_raises(tmp_path):
    p = _write(tmp_path / "tax.yaml", "- just a list\n- of strings\n")
    with pytest.raises(TaxonomyLoadError, match="must be a mapping"):
        load_taxonomy(p)


def test_taxonomy_body_not_a_mapping_raises(tmp_path):
    """``taxonomy: "string"`` is rejected — body must be a mapping."""
    p = _write(tmp_path / "tax.yaml", "taxonomy: just-a-string\n")
    with pytest.raises(TaxonomyLoadError, match="expected a `taxonomy` mapping"):
        load_taxonomy(p)


def test_category_entry_not_a_mapping_raises(tmp_path):
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories:\n    - just-a-string\n",
    )
    with pytest.raises(TaxonomyLoadError, match="must be a mapping"):
        load_taxonomy(p)


def test_label_not_a_string_raises(tmp_path):
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories:\n    - id: hr\n      label: 42\n      description: ok\n",
    )
    with pytest.raises(TaxonomyLoadError, match="non-empty `label`"):
        load_taxonomy(p)


def test_subcategories_not_a_list_raises(tmp_path):
    p = _write(
        tmp_path / "tax.yaml",
        "taxonomy:\n  categories:\n    - id: hr\n      label: People\n"
        "      description: ok\n      subcategories: just-a-string\n",
    )
    with pytest.raises(TaxonomyLoadError, match="must be a list"):
        load_taxonomy(p)
