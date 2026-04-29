from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.storage_service import InMemoryStorageService


def test_semantic_extraction_and_markdown_include_required_frontmatter():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("risk-register.txt", "text/plain", b"Risk: supplier delay")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())
    raw = jobs.extract(document_id=version.document_id, version_id=version.id)

    semantic = SemanticExtractor().extract(version=version, raw_extraction=raw)
    markdown = MarkdownGenerator().render(version=version, semantic=semantic, raw_extraction=raw)

    assert 'document_id: "' in markdown
    assert f'version_id: "{version.id}"' in markdown
    assert f'sha256: "{version.sha256}"' in markdown
    assert 'parser: "plain_text"' in markdown
    assert 'validation_status: "needs_review"' in markdown
    assert "## Source Lineage" in markdown
    assert "Risk: supplier delay" in markdown
