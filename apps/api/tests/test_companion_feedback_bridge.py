"""Schema + threshold-trigger tests for the AURA feedback bridge
(#371 / ADR-029).

The route doesn't exist yet — this PR ships the contract:

* request / record schemas pinned (field set, constraints, reaction
  enum closed),
* settings defaults pinned,
* audit event names pinned (operators encode dashboards against
  them; renames are breaking),
* the pure aggregate function ``should_trigger_re_review`` exercised
  for the documented threshold + window + dedupe rules.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.companion_feedback import (
    CompanionFeedbackRecord,
    CompanionFeedbackRequest,
)
from app.services.companion.feedback_bridge import (
    AUDIT_EVENT_FEEDBACK_RECORDED,
    AUDIT_EVENT_FEEDBACK_TRIGGERED_REVIEW,
    should_trigger_re_review,
)
from app.settings import Settings


def _request(**overrides: object) -> CompanionFeedbackRequest:
    base: dict[str, object] = {
        "answer_id": "ans_01J000",
        "citation_index": 0,
        "reaction": "wrong",
    }
    base.update(overrides)
    return CompanionFeedbackRequest(**base)  # type: ignore[arg-type]


def _record(
    *,
    chunk_id: str = "c-1",
    reaction: str = "wrong",
    user_subject: str | None = "user-1",
    recorded_at: datetime | None = None,
) -> CompanionFeedbackRecord:
    return CompanionFeedbackRecord(
        answer_id="ans_x",
        citation_index=0,
        chunk_id=chunk_id,
        document_id="d-1",
        version_id="v-1",
        reaction=reaction,  # type: ignore[arg-type]
        user_subject=user_subject,
        recorded_at=recorded_at or datetime.now(UTC),
    )


class TestCompanionFeedbackRequest:
    def test_minimal_request_round_trips(self):
        r = _request()
        assert r.note is None

    def test_reaction_enum_is_closed(self):
        for ok in ("helpful", "wrong", "incomplete"):
            _request(reaction=ok)
        with pytest.raises(ValidationError):
            _request(reaction="upvote")

    def test_citation_index_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            _request(citation_index=-1)

    def test_note_has_a_length_ceiling(self):
        # 2000 chars OK, 2001 not.
        _request(note="x" * 2000)
        with pytest.raises(ValidationError):
            _request(note="x" * 2001)

    def test_request_serialises_with_documented_field_set(self):
        body = _request(note="too vague").model_dump()
        assert set(body) == {"answer_id", "citation_index", "reaction", "note"}


class TestCompanionFeedbackRecord:
    def test_record_round_trips_with_resolved_chunk_fields(self):
        r = _record()
        # Resolved (denormalised) fields are required so aggregation
        # queries don't need a join back through the answer table.
        assert r.chunk_id == "c-1"
        assert r.document_id == "d-1"
        assert r.version_id == "v-1"

    def test_record_serialises_with_documented_field_set(self):
        body = _record().model_dump()
        assert set(body) == {
            "answer_id",
            "citation_index",
            "chunk_id",
            "document_id",
            "version_id",
            "reaction",
            "note",
            "user_subject",
            "recorded_at",
        }

    def test_user_subject_can_be_none_for_anonymous_deployments(self):
        r = _record(user_subject=None)
        assert r.user_subject is None


class TestSettingsDefaults:
    def test_threshold_defaults_to_three(self):
        assert Settings().companion_feedback_wrong_threshold == 3

    def test_window_defaults_to_fourteen_days(self):
        assert Settings().companion_feedback_window_days == 14

    def test_threshold_can_be_tuned_via_env(self, monkeypatch):
        monkeypatch.setenv("KW_COMPANION_FEEDBACK_WRONG_THRESHOLD", "5")
        assert Settings().companion_feedback_wrong_threshold == 5

    def test_threshold_must_be_positive(self, monkeypatch):
        monkeypatch.setenv("KW_COMPANION_FEEDBACK_WRONG_THRESHOLD", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_window_must_be_positive(self, monkeypatch):
        monkeypatch.setenv("KW_COMPANION_FEEDBACK_WINDOW_DAYS", "0")
        with pytest.raises(ValidationError):
            Settings()


class TestShouldTriggerReReview:
    NOW = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    def _within(self, days_ago: int) -> datetime:
        return self.NOW - timedelta(days=days_ago)

    def test_returns_false_when_under_threshold(self):
        records = [
            _record(user_subject="u-1", recorded_at=self._within(1)),
            _record(user_subject="u-2", recorded_at=self._within(2)),
        ]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is False
        )

    def test_returns_true_when_threshold_met_by_distinct_users(self):
        records = [
            _record(user_subject=f"u-{i}", recorded_at=self._within(i + 1)) for i in range(3)
        ]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is True
        )

    def test_one_user_spamming_does_not_trip_the_trigger(self):
        """ADR-029: per-subject dedupe lifts the threshold from 'N
        reactions' to 'N independent users disagreed'."""
        records = [_record(user_subject="u-1", recorded_at=self._within(i + 1)) for i in range(5)]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is False
        )

    def test_anonymous_reactions_each_count_independently(self):
        """No subject id → can't dedupe. The signal is noisier but
        unauth deployments still get a trigger path."""
        records = [_record(user_subject=None, recorded_at=self._within(i + 1)) for i in range(3)]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is True
        )

    def test_records_outside_window_are_ignored(self):
        # Three wrong reactions but they're all > 14 days old.
        records = [
            _record(user_subject=f"u-{i}", recorded_at=self._within(15 + i)) for i in range(3)
        ]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is False
        )

    def test_helpful_and_incomplete_reactions_do_not_count(self):
        records = [
            _record(reaction="helpful", user_subject=f"u-{i}", recorded_at=self._within(i + 1))
            for i in range(5)
        ] + [
            _record(reaction="incomplete", user_subject=f"v-{i}", recorded_at=self._within(i + 1))
            for i in range(5)
        ]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is False
        )

    def test_records_for_other_chunks_are_ignored(self):
        """Caller may pass a broader slice; the predicate filters."""
        records = [
            _record(chunk_id="c-other", user_subject=f"u-{i}", recorded_at=self._within(i + 1))
            for i in range(5)
        ]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=14,
                now=self.NOW,
            )
            is False
        )

    def test_zero_or_negative_threshold_or_window_returns_false(self):
        """Defensive — Settings rejects these via ge=1, but the
        predicate stays safe if a future caller passes raw values."""
        records = [_record(user_subject="u-1", recorded_at=self._within(1))]
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=0,
                window_days=14,
                now=self.NOW,
            )
            is False
        )
        assert (
            should_trigger_re_review(
                records,
                chunk_id="c-1",
                threshold=3,
                window_days=0,
                now=self.NOW,
            )
            is False
        )


class TestAuditEventNames:
    def test_event_names_match_existing_surface_entity_action_convention(self):
        """ADR-029: stable identifiers operators encode dashboards against."""
        assert AUDIT_EVENT_FEEDBACK_RECORDED == "companion.feedback.recorded"
        assert AUDIT_EVENT_FEEDBACK_TRIGGERED_REVIEW == "companion.feedback.triggered_review"
