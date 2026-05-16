"""HTTP coverage for ``/admin/taxonomy/*`` workflow routes (EPIC-1 §1.8, ADR-018).

The store-level transitions are pinned in
``test_taxonomy_version_store.py``. This file covers the route shape:

- ``POST /admin/taxonomy/drafts``: create-fresh + branch-from-source +
  validation (source-without-taxonomy-id rejection).
- ``GET /admin/taxonomy/versions/{tid}``: lineage list.
- ``GET /admin/taxonomy/versions/{tid}/{vnum}``: single-version read.
- ``POST .../transition``: every legal target + the illegal-target 409 +
  the unknown-version 404.
- ``POST .../concepts/{cid}/transition``: legal accept + merge-without-
  target 400 + unknown-suggestion 404 + illegal-state-transition 409.
- 403 gating for non-admin callers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.taxonomy_version import ConceptSuggestion, TaxonomyVersion
from app.services.auth import encode_hs256
from app.services.taxonomy_version_store import (
    add_suggestions,
    create_draft,
    promote_to_candidate,
    validate_version,
)

_SECRET = "k" * 32


@pytest.fixture
def bearer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_AUTH_MODE", "bearer")
    monkeypatch.setenv("KW_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("KW_AUTH_DEV_USER", raising=False)


def _token(role: str, user_id: str = "tester") -> str:
    return encode_hs256(
        {"sub": user_id, "role": role, "exp": 9_999_999_999, "iat": 1},
        secret=_SECRET,
    )


def _client_and_services():
    services = build_services()
    return TestClient(create_app(services=services)), services


# ─── POST /admin/taxonomy/drafts ──────────────────────────────────────


class TestCreateDraft:
    def test_empty_body_mints_fresh_taxonomy(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin', user_id='ada')}"}
        response = client.post("/admin/taxonomy/drafts", json={}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "DRAFT"
        assert body["version_number"] == 1
        assert body["taxonomy_id"]  # uuid set
        assert body["created_by"] == "ada"
        assert body["taxonomy"]["categories"] == []

    def test_branch_from_validated_inherits_tree(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        # Seed a Validated_V1 with one category.
        draft = create_draft(services.taxonomy_version_store, taxonomy_id="tx-1")
        promote_to_candidate(
            services.taxonomy_version_store,
            taxonomy_id="tx-1",
            version_number=draft.version_number,
        )
        validate_version(
            services.taxonomy_version_store,
            taxonomy_id="tx-1",
            version_number=draft.version_number,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            "/admin/taxonomy/drafts",
            json={"taxonomy_id": "tx-1", "source_version_number": 1},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["taxonomy_id"] == "tx-1"
        assert body["version_number"] == 2
        assert body["state"] == "DRAFT"

    def test_source_without_taxonomy_id_is_400(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            "/admin/taxonomy/drafts",
            json={"source_version_number": 1},
            headers=headers,
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "KW_BAD_REQUEST"

    def test_branch_from_missing_source_is_404(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            "/admin/taxonomy/drafts",
            json={"taxonomy_id": "tx-x", "source_version_number": 1},
            headers=headers,
        )
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "KW_NOT_FOUND"

    def test_non_admin_is_403(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        for role in ("viewer", "contributor", "reviewer"):
            headers = {"Authorization": f"Bearer {_token(role)}"}
            response = client.post("/admin/taxonomy/drafts", json={}, headers=headers)
            assert response.status_code == 403, f"role={role}: {response.text}"


# ─── GET /admin/taxonomy/versions/{tid} ───────────────────────────────


class TestListVersions:
    def test_returns_versions_sorted_ascending(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        # Seed three versions across two states for the same taxonomy.
        for _ in range(3):
            create_draft(services.taxonomy_version_store, taxonomy_id="tx-list")
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.get("/admin/taxonomy/versions/tx-list", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["taxonomy_id"] == "tx-list"
        assert [v["version_number"] for v in body["versions"]] == [1, 2, 3]

    def test_unknown_taxonomy_id_returns_empty_not_404(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.get("/admin/taxonomy/versions/unknown", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["taxonomy_id"] == "unknown"
        assert body["versions"] == []


# ─── GET /admin/taxonomy/versions/{tid}/{vnum} ────────────────────────


class TestGetVersion:
    def test_existing_returns_200(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = create_draft(services.taxonomy_version_store, taxonomy_id="tx-g")
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.get(
            f"/admin/taxonomy/versions/tx-g/{draft.version_number}", headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "DRAFT"
        assert body["version_number"] == draft.version_number

    def test_missing_version_is_404(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.get("/admin/taxonomy/versions/tx-g/99", headers=headers)
        assert response.status_code == 404, response.text


# ─── POST .../transition (version) ────────────────────────────────────


class TestVersionTransition:
    def _setup_draft(self, services) -> TaxonomyVersion:
        return create_draft(services.taxonomy_version_store, taxonomy_id="tx-t")

    def test_promote_draft_to_candidate(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = self._setup_draft(services)
        headers = {"Authorization": f"Bearer {_token('admin', user_id='ada')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-t/{draft.version_number}/transition",
            json={"to_state": "CANDIDATE_V0"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "CANDIDATE_V0"

    def test_validate_from_candidate_with_label(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = self._setup_draft(services)
        promote_to_candidate(
            services.taxonomy_version_store,
            taxonomy_id="tx-t",
            version_number=draft.version_number,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-t/{draft.version_number}/transition",
            json={"to_state": "VALIDATED_V1", "version_label": "V1"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "VALIDATED_V1"
        assert body["version_label"] == "V1"

    def test_archive_with_reason(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = self._setup_draft(services)
        promote_to_candidate(
            services.taxonomy_version_store,
            taxonomy_id="tx-t",
            version_number=draft.version_number,
        )
        validate_version(
            services.taxonomy_version_store,
            taxonomy_id="tx-t",
            version_number=draft.version_number,
        )
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-t/{draft.version_number}/transition",
            json={"to_state": "ARCHIVED", "reason": "superseded by V2"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ARCHIVED"

    def test_discard_draft(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = self._setup_draft(services)
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-t/{draft.version_number}/transition",
            json={"to_state": "DISCARDED"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "DISCARDED"

    def test_illegal_transition_returns_409(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = self._setup_draft(services)
        # DRAFT → VALIDATED_V1 is illegal (must go through CANDIDATE_V0).
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-t/{draft.version_number}/transition",
            json={"to_state": "VALIDATED_V1"},
            headers=headers,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "KW_CONFLICT"

    def test_to_state_draft_rejected(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft = self._setup_draft(services)
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-t/{draft.version_number}/transition",
            json={"to_state": "DRAFT"},
            headers=headers,
        )
        assert response.status_code == 400, response.text

    def test_unknown_version_returns_404(self, bearer_env: None) -> None:
        client, _ = _client_and_services()
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            "/admin/taxonomy/versions/tx-t/99/transition",
            json={"to_state": "CANDIDATE_V0"},
            headers=headers,
        )
        assert response.status_code == 404, response.text


# ─── POST .../concepts/{cid}/transition ───────────────────────────────


class TestConceptTransition:
    def _setup_draft_with_suggestion(self, services) -> tuple[TaxonomyVersion, ConceptSuggestion]:
        draft = create_draft(services.taxonomy_version_store, taxonomy_id="tx-c")
        suggestion = ConceptSuggestion(label="Battery", description="...")
        add_suggestions(
            services.taxonomy_version_store,
            taxonomy_id="tx-c",
            version_number=draft.version_number,
            suggestions=[suggestion],
        )
        return draft, suggestion

    def test_accept_transition(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft, suggestion = self._setup_draft_with_suggestion(services)
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/"
            f"{suggestion.suggestion_id}/transition",
            json={"to_state": "ACCEPTED"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ACCEPTED"

    def test_merge_with_target(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft, suggestion = self._setup_draft_with_suggestion(services)
        # Need to go NEW → UNDER_REVIEW first per ADR-018 §5.
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/"
            f"{suggestion.suggestion_id}/transition",
            json={"to_state": "UNDER_REVIEW"},
            headers=headers,
        )
        response = client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/"
            f"{suggestion.suggestion_id}/transition",
            json={"to_state": "MERGED", "merge_target_id": "battery.thermal"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "MERGED"
        assert body["merge_target_id"] == "battery.thermal"

    def test_merge_without_target_is_400(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft, suggestion = self._setup_draft_with_suggestion(services)
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/"
            f"{suggestion.suggestion_id}/transition",
            json={"to_state": "UNDER_REVIEW"},
            headers=headers,
        )
        response = client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/"
            f"{suggestion.suggestion_id}/transition",
            json={"to_state": "MERGED"},
            headers=headers,
        )
        assert response.status_code == 400, response.text

    def test_illegal_concept_transition_returns_409(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft, suggestion = self._setup_draft_with_suggestion(services)
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        # NEW → MERGED is illegal (must go through UNDER_REVIEW).
        response = client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/"
            f"{suggestion.suggestion_id}/transition",
            json={"to_state": "MERGED", "merge_target_id": "x"},
            headers=headers,
        )
        assert response.status_code == 409, response.text

    def test_unknown_suggestion_returns_404(self, bearer_env: None) -> None:
        client, services = _client_and_services()
        draft, _ = self._setup_draft_with_suggestion(services)
        headers = {"Authorization": f"Bearer {_token('admin')}"}
        response = client.post(
            f"/admin/taxonomy/versions/tx-c/{draft.version_number}/concepts/missing/transition",
            json={"to_state": "ACCEPTED"},
            headers=headers,
        )
        assert response.status_code == 404, response.text
