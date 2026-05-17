"""Opt-in spaCy NER enricher (#190 / closes the rest of #48).

Adds ``person`` and ``organization`` ``SemanticAsset`` rows to a
:class:`RawExtraction` using spaCy's pre-trained statistical NER. Sits
behind the same :class:`SemanticEnricher` Protocol as
:class:`RuleBasedEntityEnricher` so it lands as an additional enricher
in the chain rather than a rewrite.

Why opt-in
~~~~~~~~~~

spaCy itself is small (~50 MB) but its English statistical model
(``en_core_web_sm``) adds another ~13 MB and requires a separate
``python -m spacy download`` step in deployment. To keep the default
install slim — ``apps/api/pyproject.toml`` is consumed by every
contributor doing the in-memory unit suite — the spaCy SDK lives
behind a ``ner`` extra. Operators install it explicitly:

::

    pip install -e .[ner]
    python -m spacy download en_core_web_sm

Without that, importing this module is fine; **constructing**
:class:`SpacyNerEnricher` raises a clear ``RuntimeError`` so a
misconfigured deployment fails loudly at startup instead of silently
emitting no person/org assets.

Tests use the ``nlp`` constructor parameter to inject an in-process
fake (any callable that returns an object exposing ``ents``) so the
default unit suite does not need spaCy installed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticAsset

log = logging.getLogger(__name__)

# spaCy's ``en_core_web_sm`` emits these labels for person and
# organization mentions. We map both ``ORG`` and the broader ``NORP``
# (nationalities, religious or political groups) to ``organization``;
# operators who want to drop NORP can pass a narrower ``label_to_type``.
DEFAULT_LABEL_TO_TYPE: dict[str, str] = {
    "PERSON": "person",
    "ORG": "organization",
}

# Default model id. Small + English-only — anything heavier should be
# an explicit override at construction time, not a default surprise on
# the operator's wheel size.
DEFAULT_SPACY_MODEL = "en_core_web_sm"

# Mid-confidence default — spaCy's statistical NER is precise on common
# names but over-matches in narrative prose. Reviewers see every asset
# anyway because ``SemanticExtractor`` forces ``review_status =
# "needs_review"`` regardless of what the enricher declares.
DEFAULT_CONFIDENCE = 0.6


@runtime_checkable
class _SpacyEntity(Protocol):
    """Subset of spaCy's ``Span`` that we read."""

    text: str
    label_: str


@runtime_checkable
class _SpacyDoc(Protocol):
    """Subset of spaCy's ``Doc`` that we read."""

    ents: Iterable[_SpacyEntity]


class SpacyNerEnricher:
    """Opt-in :class:`SemanticEnricher` for ``person`` / ``organization``.

    Stateless beyond the loaded ``nlp`` callable; a single instance is
    reused across calls. Safe to share between threads as long as the
    underlying spaCy ``Language`` object is — which it is, per spaCy's
    pipeline contract.
    """

    name: str = "spacy_ner"

    def __init__(
        self,
        *,
        nlp: Any | None = None,
        model: str = DEFAULT_SPACY_MODEL,
        label_to_type: dict[str, str] | None = None,
        confidence: float = DEFAULT_CONFIDENCE,
    ) -> None:
        if nlp is None:
            try:
                import spacy  # type: ignore[import-not-found]  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "SpacyNerEnricher requires the optional ``ner`` extra. "
                    "Install with `pip install -e .[ner]` and run "
                    "`python -m spacy download en_core_web_sm`."
                ) from exc
            try:
                nlp = spacy.load(model)
            except OSError as exc:  # pragma: no cover - exercised behind the ner extra
                raise RuntimeError(
                    f"SpacyNerEnricher: spaCy model {model!r} is not "
                    "installed. Run "
                    f"`python -m spacy download {model}`."
                ) from exc
        self._nlp = nlp
        self._label_to_type = label_to_type or DEFAULT_LABEL_TO_TYPE
        self._confidence = confidence

    def enrich(
        self,
        raw_extraction: RawExtraction,
        existing_assets: list[SemanticAsset],  # noqa: ARG002 - Protocol shape
    ) -> list[SemanticAsset]:
        out: list[SemanticAsset] = []
        for section in raw_extraction.sections:
            text = section.text
            if not text:
                continue
            ref_ids = list(section.source_reference_ids)
            seen: set[tuple[str, str]] = set()
            try:
                doc = self._nlp(text)
            except Exception:  # noqa: BLE001 - boundary
                # spaCy can raise on malformed input or OOM. Failing
                # one section must not poison the rest of the document
                # — log and continue, mirroring the ``SemanticExtractor``
                # boundary's own per-enricher isolation.
                log.warning(
                    "enrichers.spacy_ner.section_failed",
                    extra={"section_id": section.id},
                )
                continue
            for entity in getattr(doc, "ents", []) or []:
                label = getattr(entity, "label_", None)
                text_value = getattr(entity, "text", None)
                if not isinstance(label, str) or not isinstance(text_value, str):
                    continue
                asset_type = self._label_to_type.get(label)
                if asset_type is None:
                    continue
                value = " ".join(text_value.split()).strip()
                if not value:
                    continue
                key = (asset_type, value.lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    SemanticAsset(
                        type=asset_type,
                        text=value,
                        confidence=self._confidence,
                        review_status="needs_review",
                        source_reference_ids=list(ref_ids),
                    )
                )
        return out


__all__ = [
    "DEFAULT_CONFIDENCE",
    "DEFAULT_LABEL_TO_TYPE",
    "DEFAULT_SPACY_MODEL",
    "SpacyNerEnricher",
]
