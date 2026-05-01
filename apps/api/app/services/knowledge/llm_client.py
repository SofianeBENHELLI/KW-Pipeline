"""LLM client boundary for the knowledge layer (ADR-013).

The :class:`LLMClient` Protocol is the only seam between the entity
extractor (Phase 2) / chat service (Phase 3) and a concrete LLM
provider. ADR-013 commits to one provider in v1 — Anthropic Claude —
behind this Protocol so adding a second provider later is a new
implementation, not a rewrite of every call site.

Three implementations live here:

- :class:`AnthropicLLMClient` is the production wrapper. It lazy-imports
  the ``anthropic`` SDK so this module loads in environments without
  the dependency installed (e.g. minimal CI images that only run the
  unit suite without ``ANTHROPIC_API_KEY``).
- :class:`FakeLLMClient` is the in-process test double. It returns
  recorded ``(parsed_tool_input, token_usage)`` tuples in order, so
  the default ``pytest`` invocation never reaches the network.
- The :class:`LLMClient` Protocol itself is ``@runtime_checkable`` so
  tests can assert conformance with ``isinstance``.

The Protocol surface is intentionally one method: forced structured
output via tool-use. Free-text chat is added in Phase 3 as a separate
method when needed; we don't speculate the shape now.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Default model for Phase 2. ADR-013 explicitly defers the model
# choice to Phase 2; Sonnet 4.5 is the cost/quality default for
# entity extraction, with Opus reserved for the harder Phase 3 chat
# work. Callers can override via the constructor.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"

# Conservative default. Per-call override exists on the Anthropic
# implementation; the extractor uses a smaller value for short
# section-level prompts.
DEFAULT_MAX_TOKENS = 2048


@runtime_checkable
class LLMClient(Protocol):
    """One LLM call → typed structured output.

    ``complete_with_tool`` issues a single prompt that requires the
    model to invoke a named tool whose input schema is the caller's
    desired output shape. Tool-use is the supported way to force
    JSON-shaped output from Claude (and from the equivalent endpoints
    in OpenAI / Vertex), so the Protocol commits to it.

    The return value is a ``(parsed_tool_input, token_usage)`` tuple.
    ``token_usage`` is a flat ``dict[str, int]`` carrying at minimum
    ``input_tokens`` and ``output_tokens``; cache-related counters are
    included when the provider reports them, zeros otherwise.
    """

    name: str

    def complete_with_tool(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Run one structured-output call and return the parsed tool input."""


# ─── Anthropic implementation ────────────────────────────────────────────


class AnthropicLLMClient:
    """Production :class:`LLMClient` against the Anthropic Python SDK.

    The SDK is imported in ``__init__`` (not at module load) so that
    importing this module does not require ``anthropic`` to be
    installed. Tests that exercise this class set up their own SDK
    stubs; the default unit suite uses :class:`FakeLLMClient` and
    never touches this module.

    The ``tool_schema`` passed to :meth:`complete_with_tool` must be a
    JSON Schema dict suitable for Anthropic's ``tools`` API field —
    typically the ``input_schema`` half of a tool definition. The
    method wraps it in a ``tool`` block named ``"emit_structured"`` and
    forces tool use via ``tool_choice``.
    """

    name: str = "anthropic"

    def __init__(  # pragma: no cover - exercised behind pytest -m llm_integration
        self,
        *,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any = None,
    ) -> None:
        if client is None:
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "AnthropicLLMClient requires the `anthropic` package. "
                    "Install with `pip install anthropic` or use FakeLLMClient "
                    "for tests."
                ) from exc
            client = anthropic.Anthropic(api_key=api_key)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def complete_with_tool(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Run one structured-output call against Anthropic.

        Per ADR-014 §2, the static ``system`` block is wrapped as a
        single content block with ``cache_control: {"type":
        "ephemeral"}`` so repeat calls hit Anthropic's prompt cache.
        Caching is implicit — every call earns the cache treatment;
        the entity-extraction system prompt is invariant across all
        sections of all documents, which is exactly the shape the
        cache amortizes. The user portion stays in ``messages`` and
        is *not* cached, since it varies per section.
        """
        tool_name = "emit_structured"
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": (
                        "Emit the structured payload that conforms to the "
                        "provided JSON schema. Always invoke this tool; "
                        "never reply in plain text."
                    ),
                    "input_schema": tool_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )

        # Find the tool_use block. Anthropic returns a list of content
        # blocks; with ``tool_choice`` set, exactly one should be a
        # ``tool_use`` block. If not, the model misbehaved — surface as
        # an error so the extractor's warning path catches it.
        tool_input: dict[str, Any] | None = None
        for block in getattr(response, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use" and getattr(block, "name", None) == tool_name:
                raw = getattr(block, "input", None)
                if isinstance(raw, dict):
                    tool_input = raw
                    break
        if tool_input is None:
            raise RuntimeError(
                f"Anthropic response did not include a `{tool_name}` tool_use block."
            )

        usage = getattr(response, "usage", None)
        token_usage: dict[str, int] = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
        }
        return tool_input, token_usage


# ─── In-process fake (used by all default unit tests) ────────────────────


class FakeLLMClient:
    """Deterministic in-process :class:`LLMClient` for unit tests.

    Pre-load a queue of ``(parsed_tool_input, token_usage)`` responses
    via :meth:`enqueue`. Each call to :meth:`complete_with_tool` pops
    the next one in FIFO order. If the queue is empty, the call raises
    so misconfigured tests fail loudly instead of returning ``None``.

    The fake stores the ``(system, user, tool_schema)`` tuple for each
    call on :attr:`calls` so tests can assert on prompt construction
    without spinning up a real provider.
    """

    name: str = "fake"

    def __init__(
        self,
        responses: list[tuple[dict[str, Any], dict[str, int]]] | None = None,
    ) -> None:
        self._responses: deque[tuple[dict[str, Any], dict[str, int]]] = deque(responses or [])
        self.calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        parsed_tool_input: dict[str, Any],
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._responses.append((parsed_tool_input, token_usage or {}))

    def complete_with_tool(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        self.calls.append({"system": system, "user": user, "tool_schema": tool_schema})
        if not self._responses:
            raise RuntimeError(
                "FakeLLMClient: no recorded responses left to return. "
                "Call `enqueue(...)` once per expected LLM call."
            )
        return self._responses.popleft()


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_MAX_TOKENS",
    "AnthropicLLMClient",
    "FakeLLMClient",
    "LLMClient",
]
