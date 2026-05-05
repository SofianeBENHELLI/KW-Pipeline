"""Tests for ``GET /admin/config`` (sanitized configuration snapshot).

The endpoint is consumed by the Knowledge Forge Settings widget
(``apps/_shared/settings-hub``). Two contract guarantees this suite
locks in:

1. **Default-deployment posture.** With no env set, the response shows
   every Phase-2/3/NER/audit/ITEROP feature as disabled and surfaces
   sensible defaults for upload + logging.
2. **No secret ever leaks.** When operators set Anthropic / Voyage /
   ITEROP / Neo4j credentials, the response reports
   ``configured: True`` but the raw key/token/password is not present
   anywhere in the JSON body.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``KW_*`` (and the legacy aliases) so each test
    starts from a known-empty environment regardless of contributor
    shell exports."""
    for key in (
        "KW_KNOWLEDGE_LAYER_ENABLED",
        "KW_NEO4J_URI",
        "KW_NEO4J_USER",
        "KW_NEO4J_PASSWORD",
        "KW_NEO4J_DATABASE",
        "KW_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "KW_ANTHROPIC_MODEL",
        "KW_LLM_MODEL",
        "KW_VOYAGE_API_KEY",
        "VOYAGE_API_KEY",
        "KW_EMBEDDING_MODEL",
        "KW_TAXONOMY_PATH",
        "KW_TAXONOMY_COSINE_THRESHOLD",
        "KW_NER_ENABLED",
        "KW_NER_SPACY_MODEL",
        "KW_AUDIT_ENABLED",
        "KW_AUDIT_DB_PATH",
        "KW_HITL_DEFAULT_VALIDATION_METHOD",
        "KW_ITEROP_ENABLED",
        "KW_ITEROP_WORKFLOW_REF",
        "KW_ITEROP_BASE_URL",
        "KW_ITEROP_AUTH_TOKEN",
        "KW_LOG_FORMAT",
        "KW_LOG_LEVEL",
        "KW_MAX_UPLOAD_BYTES",
        "MAX_UPLOAD_BYTES",
        "KW_ALLOWED_CONTENT_TYPES",
        "ALLOWED_CONTENT_TYPES",
        "KW_CORS_ALLOWED_ORIGINS",
        "CORS_ALLOWED_ORIGINS",
        "KW_CORS_ALLOWED_ORIGIN_REGEX",
        "KW_PERSISTENT",
        "KW_DATA_DIR",
        "KW_ENTITY_EXTRACTOR_MAX_INPUT_TOKENS_PER_DOCUMENT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_admin_config_default_posture(clean_env: None) -> None:
    """With no env set, every optional feature is reported as off."""
    client = TestClient(create_app())

    response = client.get("/admin/config")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "v0.1"
    # Phase 2 / 3 / NER / audit / ITEROP all default-off.
    assert body["llm"]["configured"] is False
    assert body["embeddings"]["configured"] is False
    assert body["knowledge_layer"]["enabled"] is False
    assert body["knowledge_layer"]["neo4j_configured"] is False
    assert body["ner"]["enabled"] is False
    assert body["audit"]["enabled"] is False
    assert body["hitl"]["default_validation_method"] == "human"
    assert body["hitl"]["iterop"]["enabled"] is False
    assert body["hitl"]["iterop"]["base_url_configured"] is False
    assert body["hitl"]["iterop"]["auth_configured"] is False
    # EPIC-A A.8: force-auto admin override is off by default; the
    # frontend reads this field to decide whether to render the
    # corpus-wide banner.
    assert body["hitl"]["force_auto_corpus"] is False
    # Sensible non-secret defaults are surfaced.
    assert body["upload"]["max_bytes"] == 50 * 1024 * 1024
    assert body["embeddings"]["model"] == "voyage-3"
    assert body["logging"]["format"] == "text"
    assert body["logging"]["level"] == "INFO"


def test_admin_config_reports_configured_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """When operators set every credential, the response says ``configured: True``
    but the raw secret string is not present anywhere in the JSON body."""
    monkeypatch.setenv("KW_ANTHROPIC_API_KEY", "sk-ant-secret-llm-12345")
    monkeypatch.setenv("KW_VOYAGE_API_KEY", "pa-secret-voyage-67890")
    monkeypatch.setenv("KW_NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("KW_NEO4J_USER", "neo4j")
    monkeypatch.setenv("KW_NEO4J_PASSWORD", "secret-neo4j-password")
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.setenv("KW_ITEROP_ENABLED", "true")
    monkeypatch.setenv("KW_ITEROP_BASE_URL", "https://iterop.example.com")
    monkeypatch.setenv("KW_ITEROP_AUTH_TOKEN", "iterop-secret-token-abcdef")
    monkeypatch.setenv("KW_ITEROP_WORKFLOW_REF", "WF-KW-DOC-REVIEW-001")
    monkeypatch.setenv("KW_HITL_DEFAULT_VALIDATION_METHOD", "external")

    client = TestClient(create_app())
    response = client.get("/admin/config")

    assert response.status_code == 200
    body = response.json()
    assert body["llm"]["configured"] is True
    assert body["embeddings"]["configured"] is True
    assert body["knowledge_layer"]["enabled"] is True
    assert body["knowledge_layer"]["neo4j_configured"] is True
    assert body["hitl"]["default_validation_method"] == "external"
    assert body["hitl"]["iterop"]["enabled"] is True
    assert body["hitl"]["iterop"]["base_url_configured"] is True
    assert body["hitl"]["iterop"]["auth_configured"] is True
    # The workflow ref is non-secret and surfaced verbatim.
    assert body["hitl"]["iterop"]["workflow_ref"] == "WF-KW-DOC-REVIEW-001"

    serialized = json.dumps(body)
    for secret in (
        "sk-ant-secret-llm-12345",
        "pa-secret-voyage-67890",
        "secret-neo4j-password",
        "iterop-secret-token-abcdef",
    ):
        assert secret not in serialized, f"Secret leaked into /admin/config response: {secret!r}"


def test_admin_config_surfaces_non_secret_overrides(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Model ids / paths / log levels are non-secret and visible."""
    monkeypatch.setenv("KW_LLM_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("KW_EMBEDDING_MODEL", "voyage-3-large")
    monkeypatch.setenv("KW_LOG_FORMAT", "json")
    monkeypatch.setenv("KW_LOG_LEVEL", "debug")
    monkeypatch.setenv("KW_MAX_UPLOAD_BYTES", "104857600")

    client = TestClient(create_app())
    response = client.get("/admin/config")

    body = response.json()
    assert body["llm"]["model"] == "claude-opus-4-7"
    assert body["embeddings"]["model"] == "voyage-3-large"
    assert body["logging"]["format"] == "json"
    # Level is uppercased on the way out so the frontend doesn't need
    # to normalize it.
    assert body["logging"]["level"] == "DEBUG"
    assert body["upload"]["max_bytes"] == 104_857_600


def test_admin_config_surfaces_force_auto_corpus_override(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """EPIC-A A.8 (#215, ADR-023 §6): the force-auto corpus override
    is surfaced on /admin/config so the frontend can render a
    non-dismissible banner when it's active."""
    monkeypatch.setenv("KW_HITL_FORCE_AUTO_CORPUS", "true")

    client = TestClient(create_app())
    response = client.get("/admin/config")

    assert response.status_code == 200
    body = response.json()
    assert body["hitl"]["force_auto_corpus"] is True


def test_admin_config_default_dev_mode_returns_200(clean_env: None) -> None:
    """Default ``KW_AUTH_MODE=dev`` resolves to the admin dev user, so
    ``GET /admin/config`` is accessible out of the box (#83 slice 2 /
    ADR-019 §3 — admin-gated endpoint, dev mode satisfies the gate)."""
    client = TestClient(create_app())

    response = client.get("/admin/config")

    assert response.status_code == 200
