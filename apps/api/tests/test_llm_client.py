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
