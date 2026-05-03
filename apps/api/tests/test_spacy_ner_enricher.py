"""Tests for ``SpacyNerEnricher`` (#190 / closes the rest of #48).

The default ``pytest`` invocation must never depend on spaCy or its
language model — both live behind the optional ``ner`` extra. Every
test in this module injects a stub ``nlp`` callable that mimics the
shape of ``spacy.Language``: a callable returning an object with an
``ents`` iterable of objects that expose ``text`` and ``label_``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.schemas.extraction import RawExtraction, RawSection, SourceReference
from app.schemas.semantic_document import SemanticAsset
from app.services.enrichers.spacy_ner import (
    DEFAULT_LABEL_TO_TYPE,
    SpacyNerEnricher,
)

# ─── Stub spaCy shapes ───────────────────────────────────────────────


@dataclass
class _StubEntity:
    text: str
    label_: str


@dataclass
class _StubDoc:
    ents: list[_StubEntity]


def _make_nlp(plan: dict[str, list[tuple[str, str]]]):
    """Return a callable ``nlp(text)`` that emits canned ents per text.

    ``plan`` maps the input text to a list of ``(label, surface_form)``
    pairs. An unknown text returns an empty ``ents`` list.
    """

    def _nlp(text: str) -> _StubDoc:
        return _StubDoc(
            ents=[_StubEntity(text=value, label_=label) for label, value in plan.get(text, [])]
        )

    return _nlp


def _raw_extraction(*sections: RawSection) -> RawExtraction:
    return RawExtraction(
        document_version_id="ver-1",
        parser_name="text",
        parser_version="1.0",
        text="\n".join(s.text for s in sections),
        sections=list(sections),
        source_references=[],
    )


def _section(
    *,
    section_id: str = "s1",
    text: str,
    refs: list[str] | None = None,
) -> RawSection:
    return RawSection(
        id=section_id,
        heading=section_id,
        text=text,
        source_reference_ids=refs if refs is not None else ["src-1"],
    )


# ─── Behaviour tests ─────────────────────────────────────────────────


def test_emits_person_and_organization_assets():
    """spaCy ``PERSON`` ⇒ ``person`` asset; ``ORG`` ⇒ ``organization`` asset."""
    nlp = _make_nlp(
        {
            "Alice works at Acme Corp.": [
                ("PERSON", "Alice"),
                ("ORG", "Acme Corp"),
            ]
        }
    )
    enricher = SpacyNerEnricher(nlp=nlp)
    result = enricher.enrich(
        _raw_extraction(_section(text="Alice works at Acme Corp.")),
        existing_assets=[],
    )

    assert len(result) == 2
    assert {(a.type, a.text) for a in result} == {
        ("person", "Alice"),
        ("organization", "Acme Corp"),
    }
    # Refs flow through verbatim from the section.
    for asset in result:
        assert asset.source_reference_ids == ["src-1"]
        assert asset.review_status == "needs_review"


def test_unknown_labels_are_silently_dropped():
    """spaCy may emit DATE, GPE, … — only ``PERSON``/``ORG`` are mapped by default."""
    nlp = _make_nlp(
        {
            "Visited Paris on 2026-05-03.": [
                ("PERSON", "Bob"),
                ("DATE", "2026-05-03"),
                ("GPE", "Paris"),
            ]
        }
    )
    enricher = SpacyNerEnricher(nlp=nlp)
    result = enricher.enrich(
        _raw_extraction(_section(text="Visited Paris on 2026-05-03.")),
        existing_assets=[],
    )
    assert {a.text for a in result} == {"Bob"}


def test_label_to_type_override_widens_or_narrows_the_mapping():
    """Operators can map additional labels (e.g. NORP) or drop ORG."""
    nlp = _make_nlp(
        {
            "ACME and the Greens partnered.": [
                ("ORG", "ACME"),
                ("NORP", "Greens"),
            ]
        }
    )
    enricher = SpacyNerEnricher(
        nlp=nlp,
        label_to_type={"NORP": "organization"},  # ORG now ignored, NORP mapped
    )
    result = enricher.enrich(
        _raw_extraction(_section(text="ACME and the Greens partnered.")),
        existing_assets=[],
    )
    assert {a.text for a in result} == {"Greens"}


def test_duplicate_entities_within_section_are_deduplicated():
    """Same (type, value) within one section ⇒ one asset, not many."""
    nlp = _make_nlp(
        {
            "Alice met Alice and ALICE.": [
                ("PERSON", "Alice"),
                ("PERSON", "Alice"),
                ("PERSON", "ALICE"),
            ]
        }
    )
    enricher = SpacyNerEnricher(nlp=nlp)
    result = enricher.enrich(
        _raw_extraction(_section(text="Alice met Alice and ALICE.")),
        existing_assets=[],
    )
    assert len(result) == 1


def test_skips_sections_without_text():
    nlp = _make_nlp({})
    enricher = SpacyNerEnricher(nlp=nlp)
    result = enricher.enrich(
        _raw_extraction(_section(text="")),
        existing_assets=[],
    )
    assert result == []


def test_isolates_per_section_failures():
    """If spaCy raises on one section's text the others still extract."""

    def flaky_nlp(text: str):
        if text == "boom":
            raise RuntimeError("nlp blew up")
        return _StubDoc(ents=[_StubEntity(text="Carol", label_="PERSON")])

    enricher = SpacyNerEnricher(nlp=flaky_nlp)
    result = enricher.enrich(
        _raw_extraction(
            _section(section_id="s1", text="boom"),
            _section(section_id="s2", text="Carol said hi."),
        ),
        existing_assets=[],
    )
    assert len(result) == 1
    assert result[0].text == "Carol"


def test_normalises_whitespace_in_surface_form():
    nlp = _make_nlp(
        {
            "x": [
                ("PERSON", "  Alice\nMartin  "),
            ]
        }
    )
    enricher = SpacyNerEnricher(nlp=nlp)
    result = enricher.enrich(
        _raw_extraction(_section(text="x")),
        existing_assets=[],
    )
    assert result[0].text == "Alice Martin"


def test_default_label_map_is_person_and_org_only():
    assert DEFAULT_LABEL_TO_TYPE == {"PERSON": "person", "ORG": "organization"}


def test_constructor_without_spacy_installed_raises_runtime_error(monkeypatch):
    """Without the ``ner`` extra, constructing without ``nlp`` is a clear error."""
    import builtins

    real_import = builtins.__import__

    def _no_spacy(name: str, *args, **kwargs):
        if name == "spacy" or name.startswith("spacy."):
            raise ImportError("No module named 'spacy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_spacy)
    with pytest.raises(RuntimeError, match="ner"):
        SpacyNerEnricher()


# ─── Use SourceReference to satisfy RawExtraction shape ──────────────


def test_preserves_section_refs_on_each_asset():
    nlp = _make_nlp({"x": [("PERSON", "Dave")]})
    enricher = SpacyNerEnricher(nlp=nlp)
    raw = RawExtraction(
        document_version_id="ver-1",
        parser_name="text",
        parser_version="1.0",
        text="x",
        sections=[
            RawSection(
                id="s1",
                heading="A",
                text="x",
                source_reference_ids=["ref-A", "ref-B"],
            )
        ],
        source_references=[
            SourceReference(
                id="ref-A",
                document_version_id="ver-1",
                section_id="s1",
                snippet="line1",
            ),
            SourceReference(
                id="ref-B",
                document_version_id="ver-1",
                section_id="s1",
                snippet="line2",
            ),
        ],
    )
    result = enricher.enrich(raw, existing_assets=[])
    assert result[0].source_reference_ids == ["ref-A", "ref-B"]
    assert isinstance(result[0], SemanticAsset)
