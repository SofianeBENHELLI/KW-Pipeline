"""Instructor-patched LLM client factory (#438).

Sits beside :mod:`~app.services.knowledge.llm_client`; eventually
replaces it as entity / claim extractors migrate. For now only
:class:`~app.services.topic_extractor.TopicExtractor` consumes this
factory — the legacy ``LLMClient`` Protocol stays in place for the
other extractors so this PR's blast radius stays inside topic
extraction.

Why a separate module:

* The ``instructor`` import is **lazy** — ``build_instructor_client``
  is the only place that imports ``instructor`` (and the SDKs it
  patches). Test suites that don't exercise topic extraction never
  pay the import cost. Failures to install the dep surface here, not
  on every API boot.
* The provider-resolution logic mirrors :func:`_resolve_llm_provider`
  in :mod:`app.dependencies` so deployments with both keys set
  honour the same ``KW_LLM_PROVIDER`` knob (``auto`` / ``gemini`` /
  ``anthropic``). Once the legacy path is gone, the resolver consolidates
  here.

The returned client is the patched provider SDK — call
``client.create(response_model=YourPydantic, …)`` or
``client.create_with_completion(…)`` for the usage telemetry path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from app.settings import Settings

if TYPE_CHECKING:  # pragma: no cover — type-only import
    import instructor

log = logging.getLogger(__name__)


# Default models. Mirrors the constants in :mod:`app.services.knowledge.llm_client`
# so a deployment that sets neither ``KW_ANTHROPIC_MODEL`` nor
# ``KW_GEMINI_MODEL`` lands on the same models the legacy LLMClient
# would have picked. Kept local so this module can stand alone once
# the legacy path retires.
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _resolve_provider(
    settings: Settings,
) -> Literal["gemini", "anthropic"] | None:
    """Pick the active LLM provider — same posture as
    :func:`app.dependencies._resolve_llm_provider`.

    Returns ``None`` when the knowledge layer is off OR no API key is
    configured for the resolved provider.
    """
    if not settings.knowledge_layer_enabled:
        return None
    has_gemini = bool(settings.gemini_api_key.strip())
    has_anthropic = bool(settings.anthropic_api_key.strip())
    pinned = settings.llm_provider
    if pinned == "gemini":
        return "gemini" if has_gemini else None
    if pinned == "anthropic":
        return "anthropic" if has_anthropic else None
    # ``auto``: Gemini primary, Anthropic fallback (matches ADR-013 §6).
    if has_gemini:
        return "gemini"
    if has_anthropic:
        return "anthropic"
    return None


def build_instructor_client(
    settings: Settings | None = None,
) -> tuple[instructor.Instructor, str] | None:
    """Build an instructor-patched client for the active provider.

    Returns ``(patched_client, model_id)`` when a provider is
    configured, ``None`` otherwise. The ``model_id`` is what callers
    pass to ``client.create(model=…)`` and what ends up in structured
    log events — the same value the legacy LLMClient would have
    surfaced.

    Anthropic uses ``Mode.ANTHROPIC_TOOLS`` (forces tool-use); Gemini
    uses ``Mode.GENAI_STRUCTURED_OUTPUTS`` (native JSON-schema
    constrained generation). instructor picks the right mode
    automatically when constructed via ``from_provider``.
    """
    settings = settings or Settings()
    provider = _resolve_provider(settings)
    if provider is None:
        return None

    # Lazy import — the dep is only needed when topic extraction is
    # enabled. Operators who don't set an LLM key never trigger this
    # branch, so the SDK + instructor wheel can be uninstalled in
    # those deployments without breaking boot.
    import instructor  # noqa: PLC0415

    if provider == "gemini":
        model = settings.gemini_model.strip() or _DEFAULT_GEMINI_MODEL
        client = instructor.from_provider(
            f"google/{model}",
            api_key=settings.gemini_api_key.strip(),
        )
        return client, model

    # provider == "anthropic"
    model = settings.anthropic_model.strip() or _DEFAULT_ANTHROPIC_MODEL
    client = instructor.from_provider(
        f"anthropic/{model}",
        api_key=settings.anthropic_api_key.strip(),
    )
    return client, model
