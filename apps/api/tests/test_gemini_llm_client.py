"""Unit tests for ``GeminiLLMClient`` (ADR-013 §6 amendment).

Mirror the Anthropic test surface so both providers are held to the
same Protocol contract, retry semantics, and token-usage shape. The
SDK is never imported: every test injects a ``MagicMock`` shaped like
``google.genai.Client`` so ``pytest`` runs without ``google-genai``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.knowledge.llm_client import (
    DEFAULT_GEMINI_MODEL,
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
)


def _make_mock_gemini_client(
    *,
    function_args: dict | None = None,
    cached_content_tokens: int = 0,
) -> MagicMock:
    """Build a stand-in for ``genai.Client`` with one queued tool-use response."""
    function_call = MagicMock()
    function_call.name = "emit_structured"
    function_call.args = function_args if function_args is not None else {"triples": []}

    part = MagicMock()
    part.function_call = function_call
    part.text = None

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = MagicMock(
        prompt_token_count=10,
        candidates_token_count=2,
        cached_content_token_count=cached_content_tokens,
    )

    client = MagicMock()
    client.models.generate_content.return_value = response
    return client


def _make_mock_gemini_text_client(
    *,
    text_blocks: list[str] | None = None,
) -> MagicMock:
    """Build a mock client whose response carries plain text parts."""
    parts = []
    for text in text_blocks if text_blocks is not None else ["hello world"]:
        part = MagicMock()
        part.text = text
        part.function_call = None
        parts.append(part)
    content = MagicMock()
    content.parts = parts
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    response.text = "".join(text_blocks) if text_blocks else "hello world"
    response.usage_metadata = MagicMock(
        prompt_token_count=7,
        candidates_token_count=3,
        cached_content_token_count=0,
    )
    client = MagicMock()
    client.models.generate_content.return_value = response
    return client


# ─── Structured-output (function-calling) path ────────────────────────────


def test_gemini_client_returns_function_call_args():
    """Tool-use mode: Gemini emits a ``function_call``; we return its ``args``."""
    mock_client = _make_mock_gemini_client(function_args={"triples": [{"x": 1}]})
    llm = GeminiLLMClient(api_key="unused", client=mock_client)

    tool_input, usage = llm.complete_with_tool(
        system="You are a precise extraction assistant.",
        user="Extract triples from: hello.",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )

    assert tool_input == {"triples": [{"x": 1}]}
    assert usage == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == DEFAULT_GEMINI_MODEL
    assert call_kwargs["contents"] == "Extract triples from: hello."
    config = call_kwargs["config"]
    assert config["system_instruction"] == "You are a precise extraction assistant."
    assert config["tool_config"]["function_calling_config"]["mode"] == "ANY"
    assert config["tool_config"]["function_calling_config"]["allowed_function_names"] == [
        "emit_structured"
    ]
    decl = config["tools"][0]["function_declarations"][0]
    assert decl["name"] == "emit_structured"
    assert decl["parameters"] == {"type": "object", "properties": {}, "required": []}


def test_gemini_client_surfaces_cache_read_tokens():
    """``cached_content_token_count`` propagates through ``cache_read_input_tokens``."""
    mock_client = _make_mock_gemini_client(
        function_args={"triples": [{"x": 1}]},
        cached_content_tokens=400,
    )
    llm = GeminiLLMClient(api_key="unused", client=mock_client)

    _, usage = llm.complete_with_tool(
        system="static",
        user="varies",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )

    assert usage["cache_read_input_tokens"] == 400
    # Gemini does not surface a distinct cache-creation counter.
    assert usage["cache_creation_input_tokens"] == 0


def test_gemini_client_raises_when_no_function_call_returned():
    """Missing ``function_call`` part surfaces a RuntimeError for the warning path."""
    part = MagicMock()
    part.function_call = None
    part.text = "the model misbehaved and emitted text instead"
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = MagicMock(
        prompt_token_count=5,
        candidates_token_count=0,
        cached_content_token_count=0,
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = response

    llm = GeminiLLMClient(api_key="unused", client=mock_client)

    with pytest.raises(RuntimeError, match="emit_structured"):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )


def test_gemini_client_satisfies_protocol():
    mock_client = _make_mock_gemini_client()
    llm = GeminiLLMClient(api_key="unused", client=mock_client)
    assert isinstance(llm, LLMClient)


# ─── Free-text (chat) path ────────────────────────────────────────────────


def test_gemini_client_complete_text_returns_joined_text_and_usage():
    mock_client = _make_mock_gemini_text_client(
        text_blocks=["chunk one ", "chunk two"],
    )
    llm = GeminiLLMClient(api_key="unused", client=mock_client)

    answer, usage = llm.complete_text(
        system="grounded system",
        user="question?",
        max_tokens=512,
    )

    assert answer == "chunk one chunk two"
    assert usage == {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    config = call_kwargs["config"]
    # No tools / tool_config on the text path.
    assert "tools" not in config
    assert "tool_config" not in config
    assert config["system_instruction"] == "grounded system"
    assert config["max_output_tokens"] == 512
    assert call_kwargs["contents"] == "question?"


def test_gemini_client_complete_text_uses_default_max_tokens_when_none():
    mock_client = _make_mock_gemini_text_client()
    llm = GeminiLLMClient(api_key="unused", client=mock_client, max_tokens=999)
    llm.complete_text(system="s", user="u", max_tokens=None)
    config = mock_client.models.generate_content.call_args.kwargs["config"]
    assert config["max_output_tokens"] == 999


def test_gemini_client_complete_text_falls_back_to_response_text_attr():
    """If ``parts`` carries no text but ``response.text`` does, use it."""
    response = MagicMock()
    response.candidates = []
    response.text = "fallback path"
    response.usage_metadata = MagicMock(
        prompt_token_count=1,
        candidates_token_count=1,
        cached_content_token_count=0,
    )
    client = MagicMock()
    client.models.generate_content.return_value = response

    llm = GeminiLLMClient(api_key="unused", client=client)
    answer, _ = llm.complete_text(system="s", user="u")
    assert answer == "fallback path"


# ─── ADR-014 §4 retry semantics on the Gemini path ────────────────────────


def _ok_response(args: dict | None = None) -> MagicMock:
    function_call = MagicMock()
    function_call.name = "emit_structured"
    function_call.args = args if args is not None else {"triples": []}
    part = MagicMock()
    part.function_call = function_call
    part.text = None
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = MagicMock(
        prompt_token_count=1,
        candidates_token_count=1,
        cached_content_token_count=0,
    )
    return response


def _ok_text_response(text: str = "ok") -> MagicMock:
    part = MagicMock()
    part.text = text
    part.function_call = None
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    response.text = text
    response.usage_metadata = MagicMock(
        prompt_token_count=1,
        candidates_token_count=1,
        cached_content_token_count=0,
    )
    return response


# The retry classifier matches by ``type(exc).__name__`` so these
# stand-ins use the real Gemini exception class names verbatim.
class ResourceExhausted(Exception):  # noqa: N818 — name match required
    """Class-name match for the Gemini retry classifier."""


class ServiceUnavailable(Exception):  # noqa: N818
    pass


class InvalidArgument(Exception):  # noqa: N818
    """Class-name does NOT match the retryable set — must surface immediately."""


def test_gemini_retries_once_on_resource_exhausted_then_succeeds():
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        ResourceExhausted("quota"),
        _ok_response({"triples": [{"x": 1}]}),
    ]
    llm = GeminiLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        rng=lambda: 0.0,
    )

    tool_input, _ = llm.complete_with_tool(
        system="s",
        user="u",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )

    assert tool_input == {"triples": [{"x": 1}]}
    assert mock_client.models.generate_content.call_count == 2
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.0


def test_gemini_retries_once_on_service_unavailable_text_path():
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        ServiceUnavailable("upstream blip"),
        _ok_text_response("retried"),
    ]
    llm = GeminiLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        rng=lambda: 0.0,
    )

    answer, _ = llm.complete_text(system="s", user="u")
    assert answer == "retried"
    assert mock_client.models.generate_content.call_count == 2
    assert len(sleeps) == 1


def test_gemini_does_not_retry_on_invalid_argument():
    """Unknown / terminal error class names must surface immediately."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [InvalidArgument("bad schema")]
    llm = GeminiLLMClient(api_key="unused", client=mock_client, rng=lambda: 0.0)

    with pytest.raises(InvalidArgument):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )
    assert mock_client.models.generate_content.call_count == 1


def test_gemini_gives_up_after_max_retries_and_reraises():
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        ServiceUnavailable("blip 1"),
        ServiceUnavailable("blip 2"),
    ]
    llm = GeminiLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        rng=lambda: 0.0,
    )

    with pytest.raises(ServiceUnavailable):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )
    assert mock_client.models.generate_content.call_count == 2
    assert len(sleeps) == 1


def test_gemini_max_retries_zero_disables_retry_loop():
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = ResourceExhausted("quota")
    llm = GeminiLLMClient(
        api_key="unused",
        client=mock_client,
        max_retries=0,
        sleep=sleeps.append,
    )
    with pytest.raises(ResourceExhausted):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )
    assert mock_client.models.generate_content.call_count == 1
    assert sleeps == []


def test_gemini_invalid_max_retries_rejected():
    with pytest.raises(ValueError):
        GeminiLLMClient(api_key="unused", client=MagicMock(), max_retries=-1)


def test_gemini_invalid_backoff_rejected():
    with pytest.raises(ValueError):
        GeminiLLMClient(api_key="unused", client=MagicMock(), retry_backoff_seconds=-0.1)


def test_gemini_classifies_http_status_429_as_retryable():
    """Generic exception with ``status_code=429`` falls into the retry bucket."""

    class _GenericHTTPError(Exception):
        def __init__(self) -> None:
            super().__init__("rate limited")
            self.status_code = 429

    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        _GenericHTTPError(),
        _ok_response(),
    ]
    llm = GeminiLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        rng=lambda: 0.0,
    )
    llm.complete_with_tool(
        system="s",
        user="u",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )
    assert mock_client.models.generate_content.call_count == 2


def test_gemini_default_model_is_flash():
    """Default model id matches ADR-013 §6: gemini-2.5-flash (cheap + fast)."""
    mock_client = _make_mock_gemini_client()
    llm = GeminiLLMClient(api_key="unused", client=mock_client)
    llm.complete_with_tool(
        system="s",
        user="u",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )
    assert mock_client.models.generate_content.call_args.kwargs["model"] == "gemini-2.5-flash"


def test_fake_llm_client_still_satisfies_protocol_after_amendment():
    """Sanity check that adding GeminiLLMClient did not regress FakeLLMClient."""
    fake = FakeLLMClient()
    assert isinstance(fake, LLMClient)


# ─── Defensive parsing branches (no function_call args, weird parts) ─────


def test_gemini_complete_with_tool_skips_non_dict_args():
    """A function_call whose ``args`` isn't a dict is treated as missing."""
    function_call = MagicMock()
    function_call.name = "emit_structured"
    function_call.args = "not-a-dict"
    bad_part = MagicMock()
    bad_part.function_call = function_call
    bad_part.text = None
    content = MagicMock()
    content.parts = [bad_part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    response.usage_metadata = MagicMock(
        prompt_token_count=1,
        candidates_token_count=0,
        cached_content_token_count=0,
    )
    client = MagicMock()
    client.models.generate_content.return_value = response

    llm = GeminiLLMClient(api_key="unused", client=client)
    with pytest.raises(RuntimeError, match="emit_structured"):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )


def test_gemini_complete_text_skips_non_string_text_parts():
    """Defensive: a part whose ``text`` attr isn't a string is ignored."""
    odd_part = MagicMock()
    odd_part.text = None  # not a str
    odd_part.function_call = None
    good_part = MagicMock()
    good_part.text = "kept"
    good_part.function_call = None
    content = MagicMock()
    content.parts = [odd_part, good_part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    response.text = "fallback"
    response.usage_metadata = MagicMock(
        prompt_token_count=1,
        candidates_token_count=1,
        cached_content_token_count=0,
    )
    client = MagicMock()
    client.models.generate_content.return_value = response

    llm = GeminiLLMClient(api_key="unused", client=client)
    answer, _ = llm.complete_text(system="s", user="u")
    assert answer == "kept"


def test_gemini_complete_text_returns_empty_when_no_text_anywhere():
    """No text parts AND no ``response.text`` → empty answer (don't crash)."""
    response = MagicMock()
    response.candidates = []
    response.text = None
    response.usage_metadata = MagicMock(
        prompt_token_count=0,
        candidates_token_count=0,
        cached_content_token_count=0,
    )
    client = MagicMock()
    client.models.generate_content.return_value = response

    llm = GeminiLLMClient(api_key="unused", client=client)
    answer, _ = llm.complete_text(system="s", user="u")
    assert answer == ""


def test_gemini_complete_text_does_not_retry_on_invalid_argument():
    """Text path: non-retryable error surfaces immediately on the text codepath."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [InvalidArgument("bad")]
    llm = GeminiLLMClient(api_key="unused", client=mock_client, rng=lambda: 0.0)
    with pytest.raises(InvalidArgument):
        llm.complete_text(system="s", user="u")
    assert mock_client.models.generate_content.call_count == 1
