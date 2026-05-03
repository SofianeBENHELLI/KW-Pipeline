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
import random
import time
from collections import deque
from collections.abc import Callable
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

# ADR-014 §4: one exponential-backoff retry on 429 / 5xx. Two attempts
# total — the original call plus one retry. Anything beyond that is a
# real upstream incident the operator should see in logs.
DEFAULT_MAX_RETRIES = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_RETRY_BACKOFF_CAP_SECONDS = 30.0


@runtime_checkable
class LLMClient(Protocol):
    """LLM provider boundary.

    Two methods because the entity extractor (Phase 2) and the chat
    service (Phase 3) want fundamentally different response shapes:

    - ``complete_with_tool`` issues a single prompt that *requires*
      the model to invoke a named tool whose input schema is the
      caller's desired output shape. Tool-use is the supported way
      to force JSON-shaped output from Claude (and the equivalent
      endpoints in OpenAI / Vertex), so the Protocol commits to it.
    - ``complete_chat`` issues a single prompt and returns the
      model's free-text answer. No tool is bound. The chat service
      uses this to render a natural-language response over a small
      pre-retrieved set of cited chunks.

    Both return ``(payload, token_usage)`` tuples; ``token_usage`` is
    a flat ``dict[str, int]`` carrying at minimum ``input_tokens`` and
    ``output_tokens``. Cache-related counters
    (``cache_read_input_tokens``, ``cache_creation_input_tokens``)
    are included when the provider reports them, zeros otherwise.
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

    def complete_chat(
        self,
        *,
        system: str,
        user: str,
    ) -> tuple[str, dict[str, int]]:
        """Run one free-text chat call and return ``(answer_text, token_usage)``.

        The static ``system`` block is wrapped with ephemeral prompt
        caching by production implementations — the chat surface's
        system prompt (RAG instructions, citation rules) is invariant
        across queries within a session, exactly the shape the cache
        amortizes.
        """


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

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        retry_backoff_cap_seconds: float = DEFAULT_RETRY_BACKOFF_CAP_SECONDS,
        sleep: Callable[[float], None] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        if client is None:  # pragma: no cover - exercised behind pytest -m llm_integration
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
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_backoff_cap_seconds = retry_backoff_cap_seconds
        self._sleep = sleep or time.sleep
        self._rng = rng or random.random

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

        Per ADR-014 §4, transient upstream failures (HTTP 429 and the
        5xx family, plus connect/timeout exceptions surfaced by the
        SDK) are retried up to ``max_retries`` times with jittered
        exponential backoff. A ``Retry-After`` header on the offending
        response, when present, replaces the computed delay.
        """
        tool_name = "emit_structured"
        response = self._call_with_retry(
            system=system,
            user=user,
            tool_schema=tool_schema,
            tool_name=tool_name,
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

    def complete_chat(
        self,
        *,
        system: str,
        user: str,
    ) -> tuple[str, dict[str, int]]:
        """Run one free-text chat call against Anthropic.

        Same prompt-cache + retry posture as
        :meth:`complete_with_tool`: the static ``system`` block is
        wrapped with ``cache_control: {"type": "ephemeral"}`` so
        repeat calls within the 5 min cache window pay the input
        cost only once, and transient 429 / 5xx failures are
        retried per ADR-014 §4.
        """
        response = self._call_with_retry_chat(system=system, user=user)

        # Anthropic returns a list of content blocks; for a chat
        # response without tool-use, exactly one ``text`` block is
        # expected. Concatenate all text blocks defensively in case
        # the SDK introduces multi-block responses later.
        chunks: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        if not chunks:
            raise RuntimeError("Anthropic chat response did not include any text content blocks.")
        answer = "".join(chunks)

        usage = getattr(response, "usage", None)
        token_usage: dict[str, int] = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
        }
        return answer, token_usage

    def _call_with_retry(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
        tool_name: str,
    ) -> Any:
        """Issue the SDK call, retrying transient upstream failures.

        Returns the raw SDK response. ``max_retries=0`` disables the
        retry loop entirely (one attempt, no recovery), which matches
        callers that bring their own outer retry strategy.
        """
        attempt = 0
        while True:
            try:
                return self._client.messages.create(
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
            except Exception as exc:  # noqa: BLE001 - classified below
                if attempt >= self._max_retries or not _is_retryable(exc):
                    raise
                delay = self._retry_delay(exc, attempt)
                log.warning(
                    "knowledge.llm.retrying",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "delay_seconds": round(delay, 3),
                        "error_type": type(exc).__name__,
                        "status_code": getattr(exc, "status_code", None),
                    },
                )
                self._sleep(delay)
                attempt += 1

    def _call_with_retry_chat(self, *, system: str, user: str) -> Any:
        """Free-text variant of :meth:`_call_with_retry` (no tools).

        The retry classification + backoff logic is identical; the
        only difference is the SDK call shape (no ``tools`` /
        ``tool_choice`` arguments).
        """
        attempt = 0
        while True:
            try:
                return self._client.messages.create(
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
                )
            except Exception as exc:  # noqa: BLE001 - classified below
                if attempt >= self._max_retries or not _is_retryable(exc):
                    raise
                delay = self._retry_delay(exc, attempt)
                log.warning(
                    "knowledge.llm.retrying",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "delay_seconds": round(delay, 3),
                        "error_type": type(exc).__name__,
                        "status_code": getattr(exc, "status_code", None),
                        "call_kind": "chat",
                    },
                )
                self._sleep(delay)
                attempt += 1

    def _retry_delay(self, exc: Exception, attempt: int) -> float:
        """Pick the backoff duration before the next attempt.

        Honours ``Retry-After`` (in seconds) when the response carries
        one; otherwise jittered exponential backoff capped at
        :data:`DEFAULT_RETRY_BACKOFF_CAP_SECONDS`.
        """
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            return min(max(retry_after, 0.0), self._retry_backoff_cap_seconds)
        base = self._retry_backoff_seconds * (2**attempt)
        jitter = self._rng() * self._retry_backoff_seconds
        return min(base + jitter, self._retry_backoff_cap_seconds)


def _is_retryable(exc: Exception) -> bool:
    """Classify an SDK exception as transient (retryable) or terminal.

    Detection is duck-typed on :pyattr:`status_code` so we don't need
    a hard import of ``anthropic`` to recognise its exception classes.
    Connection / timeout errors that surface without a status code are
    matched by their well-known SDK class names.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    name = type(exc).__name__
    return name in {
        "APIConnectionError",
        "APITimeoutError",
        "APIConnectionTimeoutError",
    }


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract ``Retry-After`` (in seconds) from an SDK exception, if any.

    The Anthropic SDK exposes the upstream HTTP response on
    ``exc.response``. We tolerate the header being absent, malformed,
    or expressed as an HTTP-date (which we ignore — exponential
    backoff covers those).
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:  # noqa: BLE001 - tolerate non-dict header bags
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ─── In-process fake (used by all default unit tests) ────────────────────


class FakeLLMClient:
    """Deterministic in-process :class:`LLMClient` for unit tests.

    Two queues — one per Protocol method — so tests can pre-load
    structured-output responses (``enqueue``) and free-text chat
    responses (``enqueue_chat``) independently. Each call to
    :meth:`complete_with_tool` / :meth:`complete_chat` pops the
    matching queue's head in FIFO order; an empty queue raises so
    misconfigured tests fail loudly instead of returning ``None``.

    The fake stores every call's prompt arguments on :attr:`calls`
    so tests can assert on prompt construction without spinning up
    a real provider. Each entry includes a ``method`` field
    (``"complete_with_tool"`` / ``"complete_chat"``) so a single
    call list covers both code paths.
    """

    name: str = "fake"

    def __init__(
        self,
        responses: list[tuple[dict[str, Any], dict[str, int]]] | None = None,
    ) -> None:
        self._responses: deque[tuple[dict[str, Any], dict[str, int]]] = deque(responses or [])
        self._chat_responses: deque[tuple[str, dict[str, int]]] = deque()
        self.calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        parsed_tool_input: dict[str, Any],
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._responses.append((parsed_tool_input, token_usage or {}))

    def enqueue_chat(
        self,
        answer: str,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        """Pre-load a free-text response for the next ``complete_chat`` call."""
        self._chat_responses.append((answer, token_usage or {}))

    def complete_with_tool(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        self.calls.append(
            {
                "method": "complete_with_tool",
                "system": system,
                "user": user,
                "tool_schema": tool_schema,
            }
        )
        if not self._responses:
            raise RuntimeError(
                "FakeLLMClient: no recorded tool responses left to return. "
                "Call `enqueue(...)` once per expected complete_with_tool call."
            )
        return self._responses.popleft()

    def complete_chat(
        self,
        *,
        system: str,
        user: str,
    ) -> tuple[str, dict[str, int]]:
        self.calls.append(
            {
                "method": "complete_chat",
                "system": system,
                "user": user,
            }
        )
        if not self._chat_responses:
            raise RuntimeError(
                "FakeLLMClient: no recorded chat responses left to return. "
                "Call `enqueue_chat(...)` once per expected complete_chat call."
            )
        return self._chat_responses.popleft()


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_RETRY_BACKOFF_CAP_SECONDS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "AnthropicLLMClient",
    "FakeLLMClient",
    "LLMClient",
]
