"""LLM client boundary for the knowledge layer (ADR-013).

The :class:`LLMClient` Protocol is the only seam between the entity
extractor (Phase 2) / chat service (Phase 3) and a concrete LLM
provider. ADR-013 §6 (amendment, 2026-05-05) extends the original
Anthropic-only commitment to a two-provider posture: Gemini is the
primary in deployments that opt into it, Anthropic remains the
fallback. Both providers sit behind this Protocol so call sites do
not change when the active provider does.

Implementations:

- :class:`AnthropicLLMClient` is a production wrapper around the
  ``anthropic`` SDK. Lazy-imports the SDK so this module loads in
  environments without the dependency installed (e.g. minimal CI
  images that only run the unit suite without ``ANTHROPIC_API_KEY``).
- :class:`GeminiLLMClient` is the production wrapper around the
  ``google-genai`` SDK. Same lazy-import posture; refuses to
  construct without ``GEMINI_API_KEY``. Maps the Anthropic-shaped
  Protocol contract onto Gemini's function-calling + free-text
  surface so call sites are unchanged.
- :class:`FakeLLMClient` is the in-process test double. It returns
  recorded ``(parsed_tool_input, token_usage)`` tuples in order, so
  the default ``pytest`` invocation never reaches the network.
- The :class:`LLMClient` Protocol itself is ``@runtime_checkable`` so
  tests can assert conformance with ``isinstance``.

The Protocol surface has two methods today:

- :meth:`LLMClient.complete_with_tool` — Phase 2 entity extraction.
  Forces structured output via tool-use; the model is required to
  invoke a named tool whose ``input_schema`` is the desired shape.
- :meth:`LLMClient.complete_text` — Phase 3 chat. Free-text
  generation for the grounded chat surface; the chat service builds
  a context-augmented prompt and calls this method to produce a
  natural-language answer.
"""

from __future__ import annotations

import logging
import random
import threading
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

# Default Gemini model. ``gemini-2.5-flash`` is the cheap + fast tier
# that mirrors Sonnet's cost/quality slot for entity extraction.
# Callers can override via the constructor or ``KW_GEMINI_MODEL``.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

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

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        """Run one free-text call and return ``(answer_text, token_usage)``.

        Phase 3 chat surface. ``max_tokens`` is optional; ``None``
        means "use the implementation's default". The returned text
        is the joined contents of every ``text`` block the model
        produced. ``token_usage`` follows the same shape as
        :meth:`complete_with_tool`.
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
        timeout_seconds: float | None = None,
        max_concurrent: int = 4,
        sleep: Callable[[float], None] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if client is None:  # pragma: no cover - exercised behind pytest -m llm_integration
            try:
                import anthropic  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "AnthropicLLMClient requires the `anthropic` package. "
                    "Install with `pip install anthropic` or use FakeLLMClient "
                    "for tests."
                ) from exc
            # Without an explicit timeout the SDK inherits httpx's default
            # (no read timeout). One slow LLM call would then hold a
            # worker forever and surface as an "API hang" — see uptime
            # plan #2.
            client_kwargs: dict[str, Any] = {"api_key": api_key}
            if timeout_seconds is not None and timeout_seconds > 0:
                client_kwargs["timeout"] = timeout_seconds
            client = anthropic.Anthropic(**client_kwargs)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_backoff_cap_seconds = retry_backoff_cap_seconds
        # Bound concurrent in-flight calls so a burst of validations
        # cannot fan out beyond the provider's per-minute rate limit.
        # Acquired around the SDK call only (not the backoff sleep) so
        # a stalled retry doesn't artificially throttle other callers.
        # ``threading.Semaphore`` because the SDK calls are blocking
        # and may run from the FastAPI threadpool or any other thread.
        self._max_concurrent = max_concurrent
        self._concurrency_semaphore = threading.Semaphore(max_concurrent)
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

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        """Run one free-text call against Anthropic.

        Phase 3 chat surface. The system prompt is wrapped with the
        same ``cache_control: ephemeral`` block as
        :meth:`complete_with_tool` (ADR-014 §2) — chat re-uses the
        invariant grounding instructions across questions, so the
        cache amortization applies the same way.

        Retry semantics mirror :meth:`complete_with_tool`: 429s and
        5xxes get one jittered exponential-backoff retry by default,
        ``Retry-After`` is honoured, and connect/timeout errors are
        classified as transient.
        """
        response = self._call_text_with_retry(
            system=system,
            user=user,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )

        # Concatenate every ``text`` block. Anthropic returns a list of
        # content blocks; for free-text completions there is usually
        # exactly one but we tolerate the multi-block shape so callers
        # receive the full answer if the model decides to emit it in
        # parts.
        parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        answer = "".join(parts)

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
                with self._concurrency_semaphore:
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

    def _call_text_with_retry(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> Any:
        """Issue a free-text SDK call, retrying transient upstream failures.

        Mirrors :meth:`_call_with_retry` but does not pass ``tools`` or
        ``tool_choice`` — the call returns plain text content blocks.
        """
        attempt = 0
        while True:
            try:
                with self._concurrency_semaphore:
                    return self._client.messages.create(
                        model=self._model,
                        max_tokens=max_tokens,
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
                        "call_kind": "complete_text",
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


# ─── Gemini implementation (ADR-013 §6 amendment) ────────────────────────


# Gemini exception class names that map onto ADR-014 §4's "transient"
# bucket. Matched by ``type(exc).__name__`` so we don't need a hard
# import of ``google.api_core.exceptions`` to recognise them.
_GEMINI_RETRYABLE_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "ResourceExhausted",  # 429-equivalent (gRPC code 8)
        "ServiceUnavailable",  # 503-equivalent (gRPC code 14)
        "InternalServerError",  # 500-equivalent (gRPC code 13)
        "DeadlineExceeded",  # 504-equivalent (gRPC code 4)
        "Aborted",  # transient conflict (gRPC code 10)
        "Unknown",  # gRPC code 2 — treat as transient
        "ServerError",  # generic API server error
        "TooManyRequests",  # alternate spelling some SDKs use
    }
)


class GeminiLLMClient:
    """Production :class:`LLMClient` against the Google Generative AI SDK.

    The SDK is imported in ``__init__`` (not at module load) so that
    importing this module does not require ``google-genai`` to be
    installed. Tests that exercise this class set up their own SDK
    stubs; the default unit suite uses :class:`FakeLLMClient` and
    never touches this module.

    The ``tool_schema`` passed to :meth:`complete_with_tool` must be a
    JSON-Schema-shaped dict suitable for Gemini's
    ``function_declaration.parameters`` field. Gemini accepts a
    subset of JSON Schema; the schemas the entity extractor emits
    (object with primitive-typed properties) fall inside that subset.

    Token usage shape is mapped to match the Anthropic surface so the
    audit logs and ADR-014 §3 circuit breaker remain provider-agnostic:

    - ``input_tokens`` ← ``prompt_token_count``
    - ``output_tokens`` ← ``candidates_token_count``
    - ``cache_read_input_tokens`` ← ``cached_content_token_count``
    - ``cache_creation_input_tokens`` ← ``0`` (Gemini does not surface
      this distinctly; context-cache *creation* is a separate API call,
      not reported in the response usage block).

    Gemini context caching (``cachedContents``) is not used in this
    revision; adding it is a follow-up that mirrors ADR-014 §2 for the
    Gemini provider.
    """

    name: str = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        retry_backoff_cap_seconds: float = DEFAULT_RETRY_BACKOFF_CAP_SECONDS,
        timeout_seconds: float | None = None,
        max_concurrent: int = 4,
        sleep: Callable[[float], None] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if client is None:  # pragma: no cover - exercised behind pytest -m llm_integration
            try:
                from google import genai  # noqa: PLC0415
                from google.genai import types as genai_types  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "GeminiLLMClient requires the `google-genai` package. "
                    "Install with `pip install google-genai` or use FakeLLMClient "
                    "for tests."
                ) from exc
            # google-genai's HttpOptions.timeout is in MILLISECONDS,
            # unlike anthropic and voyageai which take seconds. Mirror
            # the same "0/negative disables" contract those clients
            # already use so operators can flip a single env var to
            # restore SDK defaults if needed.
            client_kwargs: dict[str, Any] = {"api_key": api_key}
            if timeout_seconds is not None and timeout_seconds > 0:
                client_kwargs["http_options"] = genai_types.HttpOptions(
                    timeout=int(timeout_seconds * 1000),
                )
            client = genai.Client(**client_kwargs)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._retry_backoff_cap_seconds = retry_backoff_cap_seconds
        # See AnthropicLLMClient.__init__ for the rationale.
        self._max_concurrent = max_concurrent
        self._concurrency_semaphore = threading.Semaphore(max_concurrent)
        self._sleep = sleep or time.sleep
        self._rng = rng or random.random

    def complete_with_tool(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Run one structured-output call against Gemini.

        The Gemini equivalent of Anthropic's ``tool_use`` is a function
        declaration plus ``tool_config.function_calling_config`` set to
        ``ANY`` mode with the function name allow-listed. This forces
        the model to invoke ``emit_structured`` and return the args
        dict, matching the Protocol contract.

        Per ADR-014 §4, transient upstream failures (gRPC ``ResourceExhausted``
        / ``ServiceUnavailable`` / ``DeadlineExceeded`` and the rest of
        the transient bucket) are retried up to ``max_retries`` times
        with jittered exponential backoff.
        """
        tool_name = "emit_structured"
        response = self._call_with_retry(
            system=system,
            user=user,
            tool_schema=tool_schema,
            tool_name=tool_name,
        )

        # Find the function_call part. Gemini returns the call inside
        # ``response.candidates[*].content.parts[*].function_call``.
        # With function-calling mode ``ANY`` and the name allow-listed,
        # exactly one such part should be present; if not, the model
        # misbehaved — surface as an error so the extractor's warning
        # path catches it.
        tool_input: dict[str, Any] | None = None
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                fc = getattr(part, "function_call", None)
                if fc is not None and getattr(fc, "name", None) == tool_name:
                    raw = getattr(fc, "args", None)
                    if isinstance(raw, dict):
                        tool_input = raw
                        break
            if tool_input is not None:
                break
        if tool_input is None:
            raise RuntimeError(f"Gemini response did not include an `{tool_name}` function_call.")

        token_usage = self._extract_usage(getattr(response, "usage_metadata", None))
        return tool_input, token_usage

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        """Run one free-text call against Gemini.

        Phase 3 chat surface. The system prompt is forwarded via
        ``system_instruction`` on the request config (Gemini's
        equivalent of Anthropic's ``system`` block). Caching is not
        applied in this revision; see the class docstring.
        """
        response = self._call_text_with_retry(
            system=system,
            user=user,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )

        # Concatenate every text part. Gemini exposes ``response.text``
        # as a convenience; we still iterate over ``parts`` to tolerate
        # the multi-part shape and to remain robust against SDK
        # versions that omit the convenience accessor.
        parts: list[str] = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            convenience = getattr(response, "text", None)
            if isinstance(convenience, str):
                parts.append(convenience)
        answer = "".join(parts)

        token_usage = self._extract_usage(getattr(response, "usage_metadata", None))
        return answer, token_usage

    @staticmethod
    def _extract_usage(usage: Any) -> dict[str, int]:
        """Map Gemini's ``usage_metadata`` onto the Protocol's usage dict.

        See class docstring for the field-by-field translation.
        """
        return {
            "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
            "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cached_content_token_count", 0) or 0),
            "cache_creation_input_tokens": 0,
        }

    def _call_with_retry(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
        tool_name: str,
    ) -> Any:
        """Issue the SDK call, retrying transient upstream failures."""
        config = {
            "system_instruction": system,
            "max_output_tokens": self._max_tokens,
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": tool_name,
                            "description": (
                                "Emit the structured payload that conforms to the "
                                "provided JSON schema. Always invoke this function; "
                                "never reply in plain text."
                            ),
                            "parameters": tool_schema,
                        }
                    ]
                }
            ],
            "tool_config": {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": [tool_name],
                }
            },
        }
        attempt = 0
        while True:
            try:
                with self._concurrency_semaphore:
                    return self._client.models.generate_content(
                        model=self._model,
                        contents=user,
                        config=config,
                    )
            except Exception as exc:  # noqa: BLE001 - classified below
                if attempt >= self._max_retries or not _is_retryable_gemini(exc):
                    raise
                delay = self._retry_delay(attempt)
                log.warning(
                    "knowledge.llm.retrying",
                    extra={
                        "provider": "gemini",
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "delay_seconds": round(delay, 3),
                        "error_type": type(exc).__name__,
                        "code": getattr(exc, "code", None),
                    },
                )
                self._sleep(delay)
                attempt += 1

    def _call_text_with_retry(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> Any:
        """Issue a free-text SDK call, retrying transient upstream failures."""
        config = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
        }
        attempt = 0
        while True:
            try:
                with self._concurrency_semaphore:
                    return self._client.models.generate_content(
                        model=self._model,
                        contents=user,
                        config=config,
                    )
            except Exception as exc:  # noqa: BLE001 - classified below
                if attempt >= self._max_retries or not _is_retryable_gemini(exc):
                    raise
                delay = self._retry_delay(attempt)
                log.warning(
                    "knowledge.llm.retrying",
                    extra={
                        "provider": "gemini",
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "delay_seconds": round(delay, 3),
                        "error_type": type(exc).__name__,
                        "code": getattr(exc, "code", None),
                        "call_kind": "complete_text",
                    },
                )
                self._sleep(delay)
                attempt += 1

    def _retry_delay(self, attempt: int) -> float:
        """Jittered exponential backoff capped at the configured ceiling."""
        base = self._retry_backoff_seconds * (2**attempt)
        jitter = self._rng() * self._retry_backoff_seconds
        return min(base + jitter, self._retry_backoff_cap_seconds)


def _is_retryable_gemini(exc: Exception) -> bool:
    """Classify a Gemini SDK exception as transient (retryable) or terminal.

    Detection is duck-typed on the exception class name so this module
    does not need to import ``google.api_core.exceptions``. The set of
    names lifted to "retryable" is :data:`_GEMINI_RETRYABLE_EXCEPTION_NAMES`.

    For the (rare) case where the SDK raises a generic exception with
    an HTTP-style ``status_code``, we apply the same 429/5xx rule used
    by the Anthropic path so the retry budget stays consistent.
    """
    name = type(exc).__name__
    if name in _GEMINI_RETRYABLE_EXCEPTION_NAMES:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    return False


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
        self._text_responses: deque[tuple[str, dict[str, int]]] = deque()
        self.calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        parsed_tool_input: dict[str, Any],
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._responses.append((parsed_tool_input, token_usage or {}))

    def enqueue_text(
        self,
        answer: str,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        """Queue a free-text response for the next :meth:`complete_text` call."""
        self._text_responses.append((answer, token_usage or {}))

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

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        self.text_calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if not self._text_responses:
            raise RuntimeError(
                "FakeLLMClient: no recorded text responses left to return. "
                "Call `enqueue_text(...)` once per expected complete_text call."
            )
        return self._text_responses.popleft()


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_RETRY_BACKOFF_CAP_SECONDS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "AnthropicLLMClient",
    "FakeLLMClient",
    "GeminiLLMClient",
    "LLMClient",
]
