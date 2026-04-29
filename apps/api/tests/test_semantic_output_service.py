import pytest

from app.dependencies import build_services
from app.schemas.semantic_document import DocumentProfile, SemanticDocument


def _upload(services, content: bytes = b"policy text") -> tuple[str, str]:
    version = services.documents.upload("policy.txt", "text/plain", content)
    return version.document_id, version.id


class TestSemanticOutputServiceGenerate:
    def test_generate_caches_semantic_document_for_repeat_calls(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)

        first = services.semantic_outputs.generate(document_id=document_id, version_id=version_id)
        second = services.semantic_outputs.generate(document_id=document_id, version_id=version_id)

        assert second is first

    def test_generate_requires_a_prior_extraction(self):
        services = build_services()
        document_id, version_id = _upload(services)

        with pytest.raises(KeyError, match="Raw extraction not found"):
            services.semantic_outputs.generate(document_id=document_id, version_id=version_id)


class TestSemanticOutputServiceLookup:
    def test_get_returns_cached_semantic_document(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        generated = services.semantic_outputs.generate(
            document_id=document_id, version_id=version_id
        )

        fetched = services.semantic_outputs.get(document_id=document_id, version_id=version_id)

        assert fetched is generated

    def test_get_raises_when_no_semantic_output_was_generated(self):
        services = build_services()
        document_id, version_id = _upload(services)

        with pytest.raises(KeyError, match="Semantic output not found"):
            services.semantic_outputs.get(document_id=document_id, version_id=version_id)

    def test_get_markdown_returns_rendered_markdown(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        services.semantic_outputs.generate(document_id=document_id, version_id=version_id)

        markdown = services.semantic_outputs.get_markdown(
            document_id=document_id, version_id=version_id
        )

        assert "## Source Lineage" in markdown
        assert "policy text" in markdown

    def test_record_validation_updates_cached_validation_status(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        services.semantic_outputs.generate(document_id=document_id, version_id=version_id)

        services.semantic_outputs.record_validation(
            document_id=document_id, version_id=version_id, status="validated"
        )

        assert (
            services.semantic_outputs.get(
                document_id=document_id, version_id=version_id
            ).validation_status
            == "validated"
        )

    def test_record_validation_supports_rejected_state(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        services.semantic_outputs.generate(document_id=document_id, version_id=version_id)

        result = services.semantic_outputs.record_validation(
            document_id=document_id, version_id=version_id, status="rejected"
        )

        assert result.validation_status == "rejected"

    def test_record_validation_raises_when_no_semantic_output_exists(self):
        services = build_services()
        document_id, version_id = _upload(services)

        with pytest.raises(KeyError, match="Semantic output not found"):
            services.semantic_outputs.record_validation(
                document_id=document_id, version_id=version_id, status="validated"
            )

    def test_get_markdown_raises_when_cached_semantic_lacks_markdown(self):
        """Defensive branch: a persisted SemanticDocument without rendered
        Markdown must surface as 'Markdown output not found.' rather than
        returning None."""
        services = build_services()
        document_id, version_id = _upload(services)

        # Bypass `generate()` and store a markdown-less semantic document
        # directly via the catalog so we exercise the `markdown is None` guard.
        services.documents.catalog.save_semantic_document(
            version_id,
            SemanticDocument(
                document_version_id=version_id,
                document_profile=DocumentProfile(title="Policy"),
                markdown=None,
            ),
        )

        with pytest.raises(KeyError, match="Markdown output not found"):
            services.semantic_outputs.get_markdown(document_id=document_id, version_id=version_id)
