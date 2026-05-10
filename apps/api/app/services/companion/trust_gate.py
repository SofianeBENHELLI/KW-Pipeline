"""Default-deny trust gate for AURA companion citations (#372 / ADR-029).

The gate is a pure function applied between the search/retrieval
step and the answer-synthesis step in the future
``POST /companion/answer`` route. Lifting it out as a standalone
service so:

* the policy is testable in isolation,
* the future route is a thin call-site,
* alternate companions (recommend / decide / act, EPIC #373) reuse
  the same gate without copy-paste.

Trust posture (see ADR-029):

* **Default-deny.** Citations are kept only when the source chunk's
  latest version is ``VALIDATED`` OR the chunk itself is
  ``is_source_backed``. Everything else is filtered.
* **End-user widen toggle.** When the operator-side
  ``KW_COMPANION_TRUST_GATE_STRICT`` is ``False``, the per-call
  ``widen=True`` argument bypasses the filter so a user toggle in
  the companion UI can surface candidate knowledge.
* **Operator lock-on.** When ``KW_COMPANION_TRUST_GATE_STRICT`` is
  ``True`` (the default), the gate ignores ``widen=True`` — regulated
  deployments can be confident no candidate knowledge ever ships in
  a grounded answer regardless of UI state.

The gate emits a "no validated knowledge supports this question"
signal when it filters every candidate out, so the route can return
``KW_COMPANION_NO_VALIDATED_KNOWLEDGE`` (errors.ErrorCode) rather
than fabricating an answer from filtered-away content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HasTrustFlags(Protocol):
    """Structural typing for anything carrying the trust labels —
    works for ``ExploreSearchChunk``, ``ExploreSearchDocument``,
    ``Citation``, and any future companion-time intermediate. The
    fields mirror the names locked by ADR-029."""

    validation_status: str | None
    is_source_backed: bool


@dataclass(frozen=True, slots=True)
class TrustGateOutcome:
    """Result of running :func:`apply_trust_gate` over a candidate set.

    ``filtered_count`` powers the :class:`~app.schemas.companion.TrustSummary`
    field of the same name so the UI can render \"N candidate sources
    hidden — toggle to widen\" rather than silently dropping.
    """

    kept: list[HasTrustFlags]
    filtered_count: int


def is_trusted(item: HasTrustFlags) -> bool:
    """Single-item trust check used as the building block.

    Mirrors the explorer search panel's ``isVisible`` predicate so
    the companion's gate stays consistent with what the search bar
    shows. Centralised here so a future change (e.g. promote
    ``REJECTED`` to its own bucket) flips both surfaces together.
    """
    return item.validation_status == "VALIDATED" or item.is_source_backed


def apply_trust_gate(
    candidates: list[HasTrustFlags],
    *,
    widen: bool = False,
    operator_strict: bool = True,
) -> TrustGateOutcome:
    """Apply the default-deny trust filter to a list of candidates.

    The two boolean knobs encode the policy escape hatches:

    * ``widen`` — the per-call user toggle (\"include candidate
      knowledge\"). Honoured only when the operator hasn't locked the
      gate on. ``False`` (the default) preserves default-deny.
    * ``operator_strict`` — bound to ``Settings.companion_trust_gate_strict``.
      When ``True`` (the default), the operator has locked the gate
      on and ``widen`` is ignored — used for regulated deployments.

    Returns kept items in the original order so ranking semantics are
    preserved; ``filtered_count`` is the number dropped from the
    input.
    """
    if widen and not operator_strict:
        return TrustGateOutcome(kept=list(candidates), filtered_count=0)
    kept: list[HasTrustFlags] = []
    filtered = 0
    for item in candidates:
        if is_trusted(item):
            kept.append(item)
        else:
            filtered += 1
    return TrustGateOutcome(kept=kept, filtered_count=filtered)


__all__ = [
    "HasTrustFlags",
    "TrustGateOutcome",
    "apply_trust_gate",
    "is_trusted",
]
