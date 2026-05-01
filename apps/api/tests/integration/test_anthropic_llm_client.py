"""End-to-end smoke test against the real Anthropic API.

Marked ``llm_integration`` so it is excluded from the default suite
(see ``pyproject.toml``: ``addopts = "-m 'not integration and not
llm_integration'"``). Run explicitly with::

    pytest -m llm_integration

Skipped automatically when ``ANTHROPIC_API_KEY`` is not set so
contributors who haven't opted into the real-LLM path don't see
spurious failures even if they remove the marker filter.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge.entity_extractor import EntityExtractor
from app.services.knowledge.llm_client import AnthropicLLMClient

pytestmark = pytest.mark.llm_integration


@pytest.fixture(scope="module")
def llm_client() -> AnthropicLLMClient:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set; skipping llm_integration tests")
    model = os.environ.get("KW_ANTHROPIC_MODEL", "").strip() or None
    if model:
        return AnthropicLLMClient(api_key=api_key, model=model)
    return AnthropicLLMClient(api_key=api_key)


def test_smoke_one_section_yields_at_least_one_triple(llm_client):
    extractor = EntityExtractor(llm=llm_client)

    version = DocumentVersion(
        id="ver-int",
        document_id="doc-int",
        version_number=1,
        filename="sample.txt",
        content_type="text/plain",
        file_size=100,
        sha256="0" * 64,
        storage_uri="file://fake",
        status=DocumentVersionStatus.VALIDATED,
    )
    document = Document(
        id=version.document_id,
        original_filename=version.filename,
        latest_version_id=version.id,
        versions=[version],
    )
    semantic = SemanticDocument(
        id="sem-int",
        document_version_id=version.id,
        document_profile=DocumentProfile(title="Smoke"),
        sections=[
            SemanticSection(
                id="s1",
                heading="Compliance Statement",
                text=(
                    "Acme Corp is headquartered in Brussels and operates "
                    "the QualityGuard product line. QualityGuard is "
                    "certified under ISO 9001."
                ),
                source_reference_ids=["src-1"],
            )
        ],
        validation_status="validated",
        markdown="# smoke\n",
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )

    result = extractor.extract(document=document, version=version, semantic=semantic)

    assert len(result.triples) >= 1, (
        f"Expected at least one triple from real LLM; warnings={result.warnings}"
    )
    assert result.token_usage.get("input_tokens", 0) > 0
    assert result.token_usage.get("output_tokens", 0) > 0
    for triple in result.triples:
        assert triple.source_reference_ids == ["src-1"]
        assert triple.source_section_id == "s1"
