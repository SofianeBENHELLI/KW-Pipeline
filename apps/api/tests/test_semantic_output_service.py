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

        # Per ADR-008, the schema loader is the single boundary on read, so
        # repeat calls return semantically-equal but not identical instances.
        # Identity (`id` field) and content must still match.
        assert second.id == first.id
        assert second.model_dump() == first.model_dump()

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

        # Loader rebuilds the model on each read (ADR-008), so equality is
        # by content, not identity.
        assert fetched.id == generated.id
        assert fetched.model_dump() == generated.model_dump()

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


class TestSemanticOutputServiceLoaderIntegration:
    """Per ADR-008, reads route through the schema loader: the catalog
    returns the raw JSON payload and the loader produces the typed model."""

    def test_get_routes_through_schema_loader(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        generated = services.semantic_outputs.generate(
            document_id=document_id, version_id=version_id
        )

        # Catalog exposes the raw JSON payload (a dict) — that's the boundary
        # the loader consumes.
        payload = services.documents.catalog.get_semantic_document_payload(version_id)
        assert isinstance(payload, dict)
        assert payload["schema_version"] == "v0.1"
        assert payload["id"] == generated.id

    def test_generate_falls_back_to_loader_on_cache_hit(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(document_id=document_id, version_id=version_id)

        first = services.semantic_outputs.generate(document_id=document_id, version_id=version_id)
        # Second call must read the cached payload through the loader and
        # short-circuit before re-running the extractor.
        second = services.semantic_outputs.generate(document_id=document_id, version_id=version_id)

        assert second.id == first.id
        assert isinstance(second, SemanticDocument)
