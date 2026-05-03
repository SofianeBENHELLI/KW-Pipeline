"""Unit tests for ``AnthropicLLMClient`` and ``FakeLLMClient``.

These tests do not touch the network: ``AnthropicLLMClient`` is
constructed with a mock ``client`` so the SDK boundary is exercised
in-process. The real-SDK smoke test still lives in
``tests/integration/test_anthropic_llm_client.py`` behind
``pytest -m llm_integration``.

Phase 2.1 contract under test (ADR-014 §2): the static system prompt
must be sent as a list of content blocks with
``cache_control: {"type": "ephemeral"}`` on the static portion so
repeat calls hit Anthropic's prompt cache.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.knowledge.llm_client import AnthropicLLMClient, FakeLLMClient, LLMClient


def _make_mock_anthropic_client(
    *,
    tool_input: dict | None = None,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> MagicMock:
    """Build a stand-in for ``anthropic.Anthropic`` with one queued response."""
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "emit_structured"
    tool_use_block.input = tool_input or {"triples": []}

    response = MagicMock()
    response.content = [tool_use_block]
    response.usage = MagicMock(
        input_tokens=10,
        output_tokens=2,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )

    client = MagicMock()
    client.messages.create.return_value = response
    return client


def test_anthropic_client_sends_system_with_ephemeral_cache_control():
    """ADR-014 §2: the static system block carries cache_control."""
    mock_client = _make_mock_anthropic_client()
    llm = AnthropicLLMClient(api_key="unused", client=mock_client)

    system_text = "You are a precise extraction assistant."
    tool_input, usage = llm.complete_with_tool(
        system=system_text,
        user="Extract triples from: hello.",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )

    assert tool_input == {"triples": []}
    assert usage == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }

    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs

    # The system arg must be a list of content blocks, not a bare str,
    # with cache_control: ephemeral on the (sole) static block.
    assert call_kwargs["system"] == [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # User stays in messages and is NOT cache-controlled.
    assert call_kwargs["messages"] == [{"role": "user", "content": "Extract triples from: hello."}]
    # Tool-use mechanics still wired in.
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "emit_structured"}
    assert call_kwargs["tools"][0]["name"] == "emit_structured"


def test_anthropic_client_surfaces_cache_token_counters():
    """Cache hits/misses propagate through the usage dict."""
    mock_client = _make_mock_anthropic_client(
        tool_input={"triples": [{"x": 1}]},
        cache_read=400,
        cache_creation=0,
    )
    llm = AnthropicLLMClient(api_key="unused", client=mock_client)

    _, usage = llm.complete_with_tool(
        system="static",
        user="varies",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )

    assert usage["cache_read_input_tokens"] == 400
    assert usage["cache_creation_input_tokens"] == 0


def test_anthropic_client_raises_when_no_tool_use_block_returned():
    """Missing tool_use block surfaces a RuntimeError for the extractor's warning path."""
    response = MagicMock()
    response.content = []  # model misbehaved; no tool_use block at all
    response.usage = MagicMock(
        input_tokens=5,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    llm = AnthropicLLMClient(api_key="unused", client=mock_client)

    with pytest.raises(RuntimeError, match="emit_structured"):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )


def test_anthropic_client_satisfies_protocol():
    mock_client = _make_mock_anthropic_client()
    llm = AnthropicLLMClient(api_key="unused", client=mock_client)
    assert isinstance(llm, LLMClient)


def test_fake_llm_client_records_system_as_passed():
    """FakeLLMClient stores the system arg verbatim — backwards-compatible str shape."""
    fake = FakeLLMClient()
    fake.enqueue({"triples": []}, {"input_tokens": 1, "output_tokens": 1})

    fake.complete_with_tool(
        system="static system prompt",
        user="user prompt",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )

    assert len(fake.calls) == 1
    assert fake.calls[0]["system"] == "static system prompt"
    assert fake.calls[0]["user"] == "user prompt"


# ─── ADR-014 §4: retry on 429 / 5xx ────────────────────────────────────────


class _StubAPIError(Exception):
    """Stand-in for the Anthropic SDK's HTTP-status-bearing exceptions."""

    def __init__(
        self,
        status_code: int,
        *,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        if retry_after is not None:
            self.response = MagicMock()
            self.response.headers = {"Retry-After": retry_after}


def _ok_response(tool_input: dict | None = None) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = "emit_structured"
    block.input = tool_input or {"triples": []}
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(
        input_tokens=1,
        output_tokens=1,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return response


def test_retries_once_on_429_then_succeeds():
    """ADR-014 §4: a single 429 is recovered by the built-in retry."""
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _StubAPIError(429),
        _ok_response({"triples": [{"x": 1}]}),
    ]
    llm = AnthropicLLMClient(
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
    assert mock_client.messages.create.call_count == 2
    # First (and only) backoff slept once with a positive duration.
    assert len(sleeps) == 1
    assert sleeps[0] >= 0.0


def test_retries_once_on_503_then_succeeds():
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _StubAPIError(503),
        _ok_response(),
    ]
    llm = AnthropicLLMClient(
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
    assert mock_client.messages.create.call_count == 2


def test_retry_after_header_overrides_backoff():
    """When the upstream response carries Retry-After, honour it verbatim."""
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _StubAPIError(429, retry_after="7"),
        _ok_response(),
    ]
    llm = AnthropicLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        rng=lambda: 0.0,
        retry_backoff_seconds=0.001,  # ensure header dominates
    )
    llm.complete_with_tool(
        system="s",
        user="u",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )
    assert sleeps == [7.0]


def test_does_not_retry_on_400_bad_request():
    """ADR-014 §4 limits retries to 429/5xx; a 400 must surface immediately."""
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = _StubAPIError(400)
    llm = AnthropicLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
    )
    with pytest.raises(_StubAPIError):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )
    assert mock_client.messages.create.call_count == 1
    assert sleeps == []


def test_gives_up_after_max_retries_and_reraises():
    """Two consecutive 503s with max_retries=1 ⇒ the second one re-raises."""
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _StubAPIError(503),
        _StubAPIError(503),
    ]
    llm = AnthropicLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        rng=lambda: 0.0,
    )
    with pytest.raises(_StubAPIError):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )
    assert mock_client.messages.create.call_count == 2
    assert len(sleeps) == 1


def test_max_retries_zero_disables_retry_loop():
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = _StubAPIError(429)
    llm = AnthropicLLMClient(
        api_key="unused",
        client=mock_client,
        max_retries=0,
        sleep=sleeps.append,
    )
    with pytest.raises(_StubAPIError):
        llm.complete_with_tool(
            system="s",
            user="u",
            tool_schema={"type": "object", "properties": {}, "required": []},
        )
    assert mock_client.messages.create.call_count == 1
    assert sleeps == []


def test_connection_errors_are_retryable_without_status_code():
    """Connect/timeout errors carry no status_code but are still transient."""

    class APIConnectionError(Exception):
        pass

    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        APIConnectionError("dns blip"),
        _ok_response(),
    ]
    llm = AnthropicLLMClient(
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
    assert mock_client.messages.create.call_count == 2


def test_retry_delay_is_capped():
    """Even with an absurd Retry-After, we never sleep past the cap."""
    sleeps: list[float] = []
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _StubAPIError(429, retry_after="9999"),
        _ok_response(),
    ]
    llm = AnthropicLLMClient(
        api_key="unused",
        client=mock_client,
        sleep=sleeps.append,
        retry_backoff_cap_seconds=5.0,
    )
    llm.complete_with_tool(
        system="s",
        user="u",
        tool_schema={"type": "object", "properties": {}, "required": []},
    )
    assert sleeps == [5.0]


def test_invalid_max_retries_rejected():
    with pytest.raises(ValueError):
        AnthropicLLMClient(api_key="unused", client=MagicMock(), max_retries=-1)


def test_invalid_backoff_rejected():
    with pytest.raises(ValueError):
        AnthropicLLMClient(api_key="unused", client=MagicMock(), retry_backoff_seconds=-0.1)
