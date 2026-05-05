"""HTTP coverage for ``GET /admin/audit/events`` (#206 follow-up).

Pins the read contract:

- 200 with the rows + paginated cursor + ``available_event_names``
  set when the audit store has rows (sorted DESC by ``ts_utc``).
- ``event_name`` / ``actor`` / ``since`` / ``until`` filters are
  honoured server-side.
- ``cursor`` round-trips an opaque page boundary; the next page is
  the older slice with no overlap.
- 503 ``KW_AUDIT_DISABLED`` when ``KW_AUDIT_ENABLED=false`` (the
  in-memory default).
- 403 ``KW_FORBIDDEN`` when the caller lacks the ``admin`` role.
- ``available_event_names`` mirrors the SELECT-DISTINCT projection.

Wired against an in-memory store with explicitly-pinned events so
the test is hermetic — we never depend on cascading other admin
routes to populate the audit log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.services.audit_event_store import AuditEvent, InMemoryAuditEventStore
from app.services.auth import encode_hs256

# ADR-019 §2: production secret must be ≥ 32 bytes; mirror the length
# in tests so we exercise realistic byte handling on every code path.
_SECRET = "k" * 32


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)
    monkeypatch.setenv("KW_AUDIT_ENABLED", "true")


@pytest.fixture
def disabled_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)
    monkeypatch.setenv("KW_AUDIT_ENABLED", "false")


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_store() -> tuple[TestClient, InMemoryAuditEventStore]:
    """Build an app whose ``audit_events`` is a deterministic in-mem store."""
    services = build_services()
    # ``services`` is a frozen dataclass — bypass the immutability
    # guard via ``object.__setattr__`` the same way ``__post_init__``
    # does. We need a captured handle to seed events from tests.
    store = InMemoryAuditEventStore()
    object.__setattr__(services, "audit_events", store)
    return TestClient(create_app(services=services)), store


def _make_event(
    *,
    name: str,
    ts: datetime,
    actor: str | None = "alice",
    document_id: str | None = "doc-1",
    extra_payload: dict | None = None,
) -> AuditEvent:
    payload: dict = {"document_id": document_id}
    if actor is not None:
        payload["actor"] = actor
    if extra_payload:
        payload.update(extra_payload)
    return AuditEvent(
        event_name=name,
        level="INFO",
        ts_utc=ts,
        document_id=document_id,
        version_id=None,
        payload=payload,
    )


# ─── 200 path: full envelope ─────────────────────────────────────────


class TestListEvents:
    def test_returns_rows_sorted_desc_with_available_event_names(self, bearer_env: None) -> None:
        client, store = _client_and_store()
        base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        store.append(_make_event(name="review.validated", ts=base))
        store.append(_make_event(name="routing.decided", ts=base + timedelta(minutes=1)))
        store.append(_make_event(name="review.validated", ts=base + timedelta(minutes=2)))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events", headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 3
        # DESC by created_at — newest first.
        timestamps = [item["created_at"] for item in body["items"]]
        assert timestamps == sorted(timestamps, reverse=True)
        # Distinct event names regardless of duplicates.
        assert body["available_event_names"] == [
            "review.validated",
            "routing.decided",
        ]
        assert body["next_cursor"] is None

    def test_filter_by_event_name(self, bearer_env: None) -> None:
        client, store = _client_and_store()
        ts = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        store.append(_make_event(name="review.validated", ts=ts))
        store.append(_make_event(name="routing.decided", ts=ts))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events?event_name=routing.decided", headers=headers)

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert {item["event_name"] for item in items} == {"routing.decided"}

    def test_filter_by_actor(self, bearer_env: None) -> None:
        client, store = _client_and_store()
        ts = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        store.append(_make_event(name="review.validated", ts=ts, actor="alice"))
        store.append(_make_event(name="review.validated", ts=ts, actor="bob"))
        store.append(_make_event(name="review.validated", ts=ts, actor=None))
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events?actor=alice", headers=headers)

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert {item["actor"] for item in items} == {"alice"}

    def test_filter_by_since_until(self, bearer_env: None) -> None:
        client, store = _client_and_store()
        base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        for i in range(5):
            store.append(
                _make_event(
                    name="review.validated",
                    ts=base + timedelta(hours=i),
                )
            )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        # since=base+1h, until=base+3h → 3 events (h=1,2,3).
        since = (base + timedelta(hours=1)).isoformat()
        until = (base + timedelta(hours=3)).isoformat()
        response = client.get(
            "/admin/audit/events",
            params={"since": since, "until": until},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 3

    def test_cursor_round_trip(self, bearer_env: None) -> None:
        client, store = _client_and_store()
        base = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
        for i in range(5):
            store.append(
                _make_event(
                    name="review.validated",
                    ts=base + timedelta(minutes=i),
                )
            )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        # First page: limit=2 → 2 newest, cursor not None.
        first = client.get("/admin/audit/events?limit=2", headers=headers)
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None
        first_ids = {item["id"] for item in first_body["items"]}

        # Second page: pass the cursor.
        second = client.get(
            f"/admin/audit/events?limit=2&cursor={first_body['next_cursor']}",
            headers=headers,
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert len(second_body["items"]) == 2
        second_ids = {item["id"] for item in second_body["items"]}
        # No overlap.
        assert first_ids.isdisjoint(second_ids)

        # Third page: 1 row left, no further cursor.
        third = client.get(
            f"/admin/audit/events?limit=2&cursor={second_body['next_cursor']}",
            headers=headers,
        )
        assert third.status_code == 200, third.text
        third_body = third.json()
        assert len(third_body["items"]) == 1
        assert third_body["next_cursor"] is None

    def test_invalid_cursor_returns_400(self, bearer_env: None) -> None:
        client, _ = _client_and_store()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events?cursor=garbage", headers=headers)

        assert response.status_code == 400, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_BAD_REQUEST"

    def test_empty_store_returns_empty_envelope(self, bearer_env: None) -> None:
        client, _ = _client_and_store()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events", headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"] == []
        assert body["available_event_names"] == []
        assert body["next_cursor"] is None

    def test_payload_round_trips_verbatim(self, bearer_env: None) -> None:
        client, store = _client_and_store()
        store.append(
            _make_event(
                name="document.archived_orphan",
                ts=datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                extra_payload={"scope_kind": "personal", "scope_ref": "u-42"},
            )
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events", headers=headers)

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["payload"]["scope_kind"] == "personal"
        assert item["payload"]["scope_ref"] == "u-42"


# ─── 503 path: KW_AUDIT_DISABLED ─────────────────────────────────────


class TestAuditDisabled:
    def test_returns_503_when_audit_disabled(self, disabled_audit_env: None) -> None:
        client, _ = _client_and_store()
        headers = {"Authorization": f"Bearer {_token('admin')}"}

        response = client.get("/admin/audit/events", headers=headers)

        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_AUDIT_DISABLED"
        assert "KW_AUDIT_ENABLED" in body["error"]["remediation"]


# ─── 403 path: non-admin caller ─────────────────────────────────────


class TestForbidden:
    def test_returns_403_when_caller_not_admin(self, bearer_env: None) -> None:
        client, _ = _client_and_store()
        headers = {"Authorization": f"Bearer {_token('reviewer')}"}

        response = client.get("/admin/audit/events", headers=headers)

        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"]["code"] == "KW_FORBIDDEN"
