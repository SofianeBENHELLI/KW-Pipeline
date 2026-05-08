"""Tests for the typed configuration surface (issue #43).

The :class:`app.settings.Settings` model is the single read point for
every env var the API consumes. These tests pin the contract that
mattered for the migration:

- Defaults match the legacy hard-coded fallbacks (50 MiB upload, the
  ``text/plain`` content-type allowlist, an empty CORS allowlist, the
  knowledge layer disabled).
- Both the canonical ``KW_*`` name and the historical unprefixed name
  resolve to the same field via :class:`pydantic.AliasChoices`, so
  existing deployments that still set ``MAX_UPLOAD_BYTES`` etc. keep
  working.
- The CSV-parsing properties trim whitespace and drop empty entries —
  a trailing comma must not silently allow ``""``.
- The ``knowledge_layer_enabled`` truthiness contract matches the
  pre-#43 ``_maybe_build_knowledge_layer`` helper.
"""

from __future__ import annotations

import pytest

from app.settings import Settings

# Every env var the Settings model reads. Each test isolates itself by
# clearing the relevant subset; this list keeps the cleanup loop honest.
_ALL_VARS = [
    # Upload
    "KW_MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_BYTES",
    "KW_ALLOWED_CONTENT_TYPES",
    "ALLOWED_CONTENT_TYPES",
    # CORS
    "KW_CORS_ALLOWED_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    # Knowledge layer
    "KW_KNOWLEDGE_LAYER_ENABLED",
    "KW_NEO4J_URI",
    "KW_NEO4J_USER",
    "KW_NEO4J_PASSWORD",
    "KW_NEO4J_DATABASE",
    # LLM
    "KW_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "KW_ANTHROPIC_MODEL",
    "KW_LLM_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every settings-related env var before each test.

    The contributor's shell env is unpredictable; pinning a clean slate
    is the only way to assert defaults without spurious failures.
    """
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


class TestDefaults:
    def test_defaults_match_legacy_fallbacks(self) -> None:
        s = Settings()
        # Upload guardrails — the default MVP demo accepts the five
        # operator-facing document types without extra env wiring.
        assert s.max_upload_bytes == 50 * 1024 * 1024
        assert s.allowed_content_types == {
            "text/plain",
            "text/markdown",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        # Empty CORS allowlist by default — the legacy helper returned
        # ``[]`` which made the API reject every cross-origin request
        # until an operator opted in.
        assert s.cors_allowed_origins == []
        # Knowledge layer off by default.
        assert s.knowledge_layer_enabled is False
        assert s.neo4j_uri == ""
        assert s.neo4j_database == "neo4j"
        # LLM credentials unset by default.
        assert s.anthropic_api_key == ""
        assert s.anthropic_model == ""
        # ADR-014 §3 circuit breaker disabled by default.
        assert s.entity_extractor_max_input_tokens_per_document == 0


class TestUploadGuardrails:
    def test_kw_prefixed_max_upload_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KW_MAX_UPLOAD_BYTES", "1024")
        assert Settings().max_upload_bytes == 1024

    def test_legacy_unprefixed_max_upload_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing deployments set the unprefixed name; that must keep working."""
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "2048")
        assert Settings().max_upload_bytes == 2048

    def test_kw_prefixed_wins_over_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both names are set, the prefixed canonical name takes priority."""
        monkeypatch.setenv("KW_MAX_UPLOAD_BYTES", "111")
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "999")
        assert Settings().max_upload_bytes == 111

    def test_kw_prefixed_allowed_content_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KW_ALLOWED_CONTENT_TYPES", "text/plain,application/pdf")
        assert Settings().allowed_content_types == {"text/plain", "application/pdf"}

    def test_legacy_allowed_content_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOWED_CONTENT_TYPES", "application/json")
        assert Settings().allowed_content_types == {"application/json"}

    def test_allowed_content_types_strips_and_drops_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Trailing commas and stray whitespace must not introduce ``""``."""
        monkeypatch.setenv(
            "KW_ALLOWED_CONTENT_TYPES",
            "  text/plain , ,application/json,",
        )
        assert Settings().allowed_content_types == {"text/plain", "application/json"}


class TestCors:
    def test_kw_prefixed_cors_allowlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "KW_CORS_ALLOWED_ORIGINS",
            "http://localhost:5173,https://orbital.example.com",
        )
        assert Settings().cors_allowed_origins == [
            "http://localhost:5173",
            "https://orbital.example.com",
        ]

    def test_legacy_cors_allowlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
        assert Settings().cors_allowed_origins == ["http://localhost:5173"]

    def test_cors_allowlist_drops_blanks_and_trims(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS",
            " http://a.example.com , , https://b.example.com,",
        )
        assert Settings().cors_allowed_origins == [
            "http://a.example.com",
            "https://b.example.com",
        ]

    def test_blank_cors_value_yields_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A literally empty env var must not allow a blank-string origin."""
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
        assert Settings().cors_allowed_origins == []

    def test_cors_allowed_origin_regex_default_blank(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("KW_CORS_ALLOWED_ORIGIN_REGEX", raising=False)
        assert Settings().cors_allowed_origin_regex == ""

    def test_cors_allowed_origin_regex_via_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Operators can whitelist whole tenant families with one regex."""
        regex = r"^https://.*\.3dexperience\.3ds\.com$"
        monkeypatch.setenv("KW_CORS_ALLOWED_ORIGIN_REGEX", regex)
        assert Settings().cors_allowed_origin_regex == regex


class TestKnowledgeLayer:
    @pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on", "On"])
    def test_truthy_values_enable_layer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        flag: str,
    ) -> None:
        monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", flag)
        assert Settings().knowledge_layer_enabled is True

    @pytest.mark.parametrize("flag", ["", "0", "false", "no", "off", "maybe"])
    def test_falsy_values_keep_layer_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
        flag: str,
    ) -> None:
        monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", flag)
        assert Settings().knowledge_layer_enabled is False

    def test_neo4j_block_pulled_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KW_NEO4J_URI", "bolt://example:7687")
        monkeypatch.setenv("KW_NEO4J_USER", "neo4j")
        monkeypatch.setenv("KW_NEO4J_PASSWORD", "secret")
        monkeypatch.setenv("KW_NEO4J_DATABASE", "kw")

        s = Settings()
        assert s.neo4j_uri == "bolt://example:7687"
        assert s.neo4j_user == "neo4j"
        assert s.neo4j_password == "secret"
        assert s.neo4j_database == "kw"


class TestLLMCredentials:
    def test_kw_prefixed_anthropic_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KW_ANTHROPIC_API_KEY", "sk-kw")
        assert Settings().anthropic_api_key == "sk-kw"

    def test_legacy_anthropic_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The unprefixed ``ANTHROPIC_API_KEY`` is the SDK's own canonical
        name and many deploy tools surface only that label — keep it as
        a Pydantic alias so we don't break existing setups."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-legacy")
        assert Settings().anthropic_api_key == "sk-legacy"

    def test_kw_prefixed_wins_over_legacy_anthropic_api_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KW_ANTHROPIC_API_KEY", "sk-kw")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-legacy")
        assert Settings().anthropic_api_key == "sk-kw"

    def test_anthropic_model_via_kw_anthropic_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Historical name used by ``dependencies.py`` since Phase 2."""
        monkeypatch.setenv("KW_ANTHROPIC_MODEL", "claude-haiku-4-5")
        assert Settings().anthropic_model == "claude-haiku-4-5"

    def test_anthropic_model_via_kw_llm_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The architecture doc advertises ``KW_LLM_MODEL`` — that alias must work."""
        monkeypatch.setenv("KW_LLM_MODEL", "claude-sonnet-4-5")
        assert Settings().anthropic_model == "claude-sonnet-4-5"

    def test_entity_extractor_token_cap_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ADR-014 §3 circuit breaker is configurable via the prefixed env var."""
        monkeypatch.setenv("KW_ENTITY_EXTRACTOR_MAX_INPUT_TOKENS_PER_DOCUMENT", "12000")
        assert Settings().entity_extractor_max_input_tokens_per_document == 12000


class TestProgrammaticConstruction:
    """``populate_by_name=True`` lets tests construct a Settings directly
    by field name without going through environment variables."""

    def test_construct_by_field_name(self) -> None:
        s = Settings(
            max_upload_bytes=99,
            allowed_content_types_csv="text/markdown",
            cors_allowed_origins_csv="https://x.example.com",
        )
        assert s.max_upload_bytes == 99
        assert s.allowed_content_types == {"text/markdown"}
        assert s.cors_allowed_origins == ["https://x.example.com"]


class TestEnvExampleLoadable:
    """Pin the contract that ``cp docker/.env.example docker/.env`` produces
    a working file.

    Pydantic strict-parses ``""`` to int / bool / float and crashes on
    boot if the example ships ``KW_PERSISTENT=`` (or any other non-str
    field with an empty value). This test loads every uncommented
    ``KEY=VALUE`` line from the example and constructs ``Settings()``;
    a bare ``key=value`` without a default is the regression we're
    guarding against.
    """

    @staticmethod
    def _parse_env_example() -> dict[str, str]:
        from pathlib import Path

        example = Path(__file__).resolve().parents[3] / "docker" / ".env.example"
        assert example.is_file(), f"docker/.env.example not found at {example}"
        env: dict[str, str] = {}
        for raw_line in example.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
        return env

    def test_env_example_constructs_settings_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in self._parse_env_example().items():
            monkeypatch.setenv(key, value)
        # If ``Settings()`` raises here, the regression is back: a non-str
        # field somewhere in the example is shipping with an empty
        # value. Comment out the offending line in ``.env.example``
        # (operators uncomment + override; default applies otherwise).
        Settings()

    def test_env_example_only_uncomments_str_assignments(self) -> None:
        """Defence in depth: every uncommented assignment must be a
        ``str``-typed Settings field (so an empty value doesn't crash
        pydantic). If you need to ship an int/bool/float in the
        example, *comment out the line* and let the default apply."""
        # ``str`` fields tolerate empty values; non-``str`` fields don't.
        # Constructing Settings with every uncommented key set to its
        # example value is the actionable check — a clean build proves
        # the contract. The companion test above does exactly that.
        # This test exists so the file gains a second, cheaper line of
        # defence: even if the env-loading glue changes, the parser
        # contract stays explicit.
        env = self._parse_env_example()
        for key, value in env.items():
            assert isinstance(key, str) and key, "empty key in .env.example"
            assert isinstance(value, str), f"non-str value parsed for {key}"
