"""Tests for the LLM-driven business taxonomy creator (EPIC-1 §1.6, #343)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.schemas.taxonomy import Taxonomy
from app.schemas.taxonomy_version import ConceptSuggestion
from app.services.knowledge.business_taxonomy_creator import (
    BusinessTaxonomyCreationFailed,
    BusinessTaxonomyCreator,
)

# ─── Fake LLM client (mirrors topic_extractor's test pattern) ──────────


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class _Completion:
    usage: _Usage = field(default_factory=_Usage)


@dataclass
class _FakeInstructor:
    """Records every call + returns a canned envelope.

    ``canned_response`` is whatever the test wants the LLM to "return";
    when it's an exception, ``create_with_completion`` raises it
    instead. Calls are recorded so tests can assert the system prompt
    + user prompt the service constructed.
    """

    canned_response: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_with_completion(
        self,
        *,
        response_model,  # type: ignore[no-untyped-def]
        messages,  # type: ignore[no-untyped-def]
        max_retries=2,  # type: ignore[no-untyped-def]
        max_tokens=4096,  # type: ignore[no-untyped-def]
    ):
        self.calls.append(
            {
                "response_model": response_model,
                "messages": messages,
                "max_retries": max_retries,
                "max_tokens": max_tokens,
            }
        )
        if isinstance(self.canned_response, Exception):
            raise self.canned_response
        # Build the requested response_model from the canned dict so the
        # service sees a real Pydantic instance.
        envelope = response_model(**self.canned_response)
        return envelope, _Completion()


def _suggestion(
    label: str,
    *,
    description: str = "From the aggregator.",
    state: str = "ACCEPTED",
    evidence_count: int = 3,
    confidence: float = 0.85,
    merge_target_id: str | None = None,
) -> ConceptSuggestion:
    return ConceptSuggestion(
        label=label,
        description=description,
        state=state,  # type: ignore[arg-type]
        evidence_chunk_ids=[f"chunk-{i}" for i in range(evidence_count)],
        confidence=confidence,
        merge_target_id=merge_target_id,
    )


# ─── Filter: only ACCEPTED + MERGED feed the LLM ───────────────────────


class TestSuggestionFilter:
    def test_empty_input_returns_empty_taxonomy_without_llm_call(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _FakeInstructor(canned_response={"categories": []})
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        caplog.set_level(logging.INFO)
        result = creator.create_from_suggestions([])
        assert isinstance(result, Taxonomy)
        assert result.categories == []
        # No LLM call made.
        assert fake.calls == []
        # Telemetry event fired.
        events = [r for r in caplog.records if r.msg.endswith("skipped_no_accepted_concepts")]
        assert events

    def test_only_new_suggestions_treated_as_empty(self) -> None:
        fake = _FakeInstructor(canned_response={"categories": []})
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        suggestions = [
            _suggestion("alpha", state="NEW"),
            _suggestion("beta", state="UNDER_REVIEW"),
            _suggestion("gamma", state="REJECTED"),
            _suggestion("delta", state="DEFERRED"),
        ]
        result = creator.create_from_suggestions(suggestions)
        assert result.categories == []
        assert fake.calls == []  # nothing accepted → no LLM

    def test_accepted_and_merged_are_passed_to_llm(self) -> None:
        fake = _FakeInstructor(
            canned_response={
                "categories": [
                    {
                        "id": "battery",
                        "label": "Battery",
                        "description": "Battery domain.",
                        "subcategories": [],
                    }
                ]
            }
        )
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        suggestions = [
            _suggestion("Battery Thermal", state="ACCEPTED"),
            _suggestion("Cooling", state="MERGED", merge_target_id="thermal.cooling"),
            _suggestion("Noise", state="REJECTED"),
        ]
        creator.create_from_suggestions(suggestions)
        assert len(fake.calls) == 1
        user_message = fake.calls[0]["messages"][-1]["content"]
        # The user prompt mentions both ACCEPTED and MERGED concepts,
        # but NOT the REJECTED one.
        assert "Battery Thermal" in user_message
        assert "Cooling" in user_message
        assert "Noise" not in user_message


# ─── Wire-shape hydration ──────────────────────────────────────────────


class TestTreeHydration:
    def test_flat_tree_hydrated_with_imposed_source(self) -> None:
        fake = _FakeInstructor(
            canned_response={
                "categories": [
                    {
                        "id": "battery",
                        "label": "Battery",
                        "description": "Battery-related concepts.",
                        "subcategories": [],
                    },
                    {
                        "id": "engineering",
                        "label": "Engineering",
                        "description": "Engineering processes.",
                        "subcategories": [],
                    },
                ]
            }
        )
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        result = creator.create_from_suggestions(
            [_suggestion("Battery"), _suggestion("Engineering")]
        )
        assert [c.id for c in result.categories] == ["battery", "engineering"]
        # ADR-017 §3: operator-triggered creates are tagged ``imposed``.
        assert all(c.source == "imposed" for c in result.categories)

    def test_nested_subcategories_preserved(self) -> None:
        fake = _FakeInstructor(
            canned_response={
                "categories": [
                    {
                        "id": "battery",
                        "label": "Battery",
                        "description": "Top-level battery domain.",
                        "subcategories": [
                            {
                                "id": "battery.thermal",
                                "label": "Thermal",
                                "description": "Thermal management subcategory.",
                                "subcategories": [
                                    {
                                        "id": "battery.thermal.cooling",
                                        "label": "Cooling",
                                        "description": "Cooling loops + glycol.",
                                        "subcategories": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        result = creator.create_from_suggestions([_suggestion("Battery Thermal Cooling")])
        # Recursive walk verified.
        battery = result.categories[0]
        thermal = battery.subcategories[0]
        cooling = thermal.subcategories[0]
        assert battery.id == "battery"
        assert thermal.id == "battery.thermal"
        assert cooling.id == "battery.thermal.cooling"
        # ``source="imposed"`` propagates through every level.
        assert all(c.source == "imposed" for c in (battery, thermal, cooling))


# ─── Prompt content ───────────────────────────────────────────────────


class TestPromptContent:
    def test_system_prompt_includes_hard_rules(self) -> None:
        fake = _FakeInstructor(canned_response={"categories": []})
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        creator.create_from_suggestions([_suggestion("Battery"), _suggestion("Thermal")])
        sys_message = fake.calls[0]["messages"][0]
        assert sys_message["role"] == "system"
        # Hard-rule numbers pin the prompt's structure so a future
        # rewrite has to reflow the tests too — guardrails for the
        # contract the LLM is asked to obey.
        assert "Hard rules" in sys_message["content"]
        assert "at most 3 levels" in sys_message["content"]
        assert "Never duplicate" in sys_message["content"]

    def test_user_prompt_lists_accepted_concepts_with_metadata(self) -> None:
        fake = _FakeInstructor(canned_response={"categories": []})
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        creator.create_from_suggestions([_suggestion("Battery", evidence_count=5, confidence=0.9)])
        user_message = fake.calls[0]["messages"][-1]["content"]
        assert "Battery" in user_message
        assert "confidence=90%" in user_message
        assert "evidence=5 chunks" in user_message


# ─── Audit + telemetry ────────────────────────────────────────────────


class TestAuditTrail:
    def test_created_event_includes_actor_when_threaded(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _FakeInstructor(
            canned_response={
                "categories": [
                    {
                        "id": "x",
                        "label": "X",
                        "description": "X.",
                        "subcategories": [],
                    }
                ]
            }
        )
        creator = BusinessTaxonomyCreator(client=fake, model="claude-sonnet")
        caplog.set_level(logging.INFO)
        creator.create_from_suggestions([_suggestion("Battery")], actor="ada")
        events = [r for r in caplog.records if r.msg == "knowledge.business_taxonomy.created"]
        assert events
        record = events[-1]
        assert getattr(record, "actor", None) == "ada"
        assert getattr(record, "llm_model", None) == "claude-sonnet"
        assert getattr(record, "accepted_count", None) == 1
        assert getattr(record, "category_count_total", None) == 1

    def test_created_event_omits_actor_when_system(self, caplog: pytest.LogCaptureFixture) -> None:
        fake = _FakeInstructor(
            canned_response={
                "categories": [
                    {
                        "id": "x",
                        "label": "X",
                        "description": "X.",
                        "subcategories": [],
                    }
                ]
            }
        )
        creator = BusinessTaxonomyCreator(client=fake, model="claude-sonnet")
        caplog.set_level(logging.INFO)
        creator.create_from_suggestions([_suggestion("Battery")])
        events = [r for r in caplog.records if r.msg == "knowledge.business_taxonomy.created"]
        assert events
        # ``actor`` key is omitted entirely (not set to None) per the
        # #91 actor-id backfill convention.
        assert not hasattr(events[-1], "actor")


# ─── Failure surface ───────────────────────────────────────────────────


class TestLLMFailure:
    def test_llm_exception_raises_creation_failed(self, caplog: pytest.LogCaptureFixture) -> None:
        fake = _FakeInstructor(canned_response=RuntimeError("upstream 500"))
        creator = BusinessTaxonomyCreator(client=fake, model="test")
        caplog.set_level(logging.WARNING)
        with pytest.raises(BusinessTaxonomyCreationFailed, match="RuntimeError: upstream 500"):
            creator.create_from_suggestions([_suggestion("Battery")])
        events = [r for r in caplog.records if r.msg == "knowledge.business_taxonomy.llm_failed"]
        assert events
        assert getattr(events[-1], "error_type", None) == "RuntimeError"


# ─── Construction validation ──────────────────────────────────────────


class TestConstruction:
    def test_max_output_tokens_must_be_at_least_256(self) -> None:
        fake = _FakeInstructor(canned_response={"categories": []})
        with pytest.raises(ValueError, match="max_output_tokens"):
            BusinessTaxonomyCreator(client=fake, model="test", max_output_tokens=100)
