"""HTTP coverage for the transitional Demo-toggle endpoints.

Pins the contract for the three operator endpoints
(``/admin/demo/{load,status,reset}``) without driving the full 30-60
second loader run — the loader's ``main(argv)`` is patched to a no-op
so each test stays fast and offline.

What this module pins:

- ``GET /admin/demo/status`` returns a fresh-state response on a
  pristine in-memory backend.
- ``POST /admin/demo/load`` returns ``202 Accepted`` and flips
  ``in_progress`` to true; the loader thread runs but does not need
  to talk to a real backend (the patched ``main`` returns 0
  immediately).
- The conflict guard refuses with ``409 DEMO_CONFLICT`` when a
  non-demo document is already present, and accepts the same call
  with ``force=true``.
- ``POST /admin/demo/reset`` flips ``archived_at`` only on demo-named
  rows and leaves non-demo rows untouched.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.services.demo_dataset import DEMO_FIXTURE_FILENAMES

# ─── Helpers ──────────────────────────────────────────────────────────


def _make_version(document_id: str, *, filename: str) -> DocumentVersion:
    """Build a STORED :class:`DocumentVersion` with a deterministic sha."""
    return DocumentVersion(
        id=f"{document_id}-v1",
        document_id=document_id,
        version_number=1,
        filename=filename,
        content_type="text/plain",
        file_size=10,
        sha256=("sha-" + document_id).ljust(64, "0"),
        storage_uri=f"memory://documents/{document_id}-v1/{filename}",
        status=DocumentVersionStatus.STORED,
    )


def _seed_document(services, document_id: str, filename: str) -> Document:
    """Persist a single-version document with the given filename."""
    version = _make_version(document_id, filename=filename)
    document = Document.with_first_version(version)
    services.documents.catalog.save_document_with_version(document, version)
    return document


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def demo_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the toggle's state file at an isolated temp dir.

    The toggle reads ``KW_DATA_DIR`` via :class:`Settings` at
    container build time, so we set the env var BEFORE
    :func:`build_services` is called by :func:`client`. The dir
    itself is created lazily by the service on first state write —
    we don't pre-create it here so we exercise that path too.
    """
    target = tmp_path / "demo-state"
    monkeypatch.setenv("KW_DATA_DIR", str(target))
    return target


@pytest.fixture
def patched_loader(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Replace the loader's ``main`` with a no-op that records its argv.

    The toggle re-imports ``load_demo_dataset.main`` inside the
    background thread (via the ``sys.path``-mutation pattern the
    rest of the codebase uses). We import the module here so the
    same module object is in ``sys.modules`` and our patch is
    observed by the thread's import.
    """
    invocations: list[tuple] = []

    # Trigger the same sys.path tweak the service does so the import
    # below resolves regardless of where pytest was launched from.
    import load_demo_dataset  # type: ignore[import-not-found]

    from app.services import demo_dataset as _demo_dataset_module  # noqa: F401

    def _fake_main(argv):  # type: ignore[no-untyped-def]
        invocations.append(tuple(argv) if argv is not None else ())
        return 0

    monkeypatch.setattr(load_demo_dataset, "main", _fake_main)
    return invocations


@pytest.fixture
def client(
    demo_data_dir: Path,
    patched_loader: list[tuple],
) -> Iterator[tuple[TestClient, object]]:
    """Build a fresh in-memory app + TestClient pair.

    ``demo_data_dir`` is requested before ``build_services`` so the
    KW_DATA_DIR env var is observed at container build time. The
    loader patch is applied before any route runs. We yield
    ``(client, services)`` so individual tests can seed catalog
    rows directly via the service container.
    """
    services = build_services()
    test_client = TestClient(create_app(services=services))
    try:
        yield test_client, services
    finally:
        test_client.close()


def _join_loader_threads() -> None:
    """Wait for any spawned loader thread to finish before assertions.

    The toggle's worker is a daemon thread so the process can exit
    without joining; tests that read the post-run state file need to
    wait deterministically. We pick threads up by name to avoid
    coupling to internal references.
    """
    for thread in threading.enumerate():
        if thread.name == "demo-toggle-loader":
            thread.join(timeout=5.0)


# ─── /admin/demo/status — fresh state ────────────────────────────────


def test_status_returns_fresh_state_on_empty_backend(
    client: tuple[TestClient, object],
) -> None:
    """A pristine backend should report no demo, no progress, no errors."""
    test_client, _ = client

    response = test_client.get("/admin/demo/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "loaded": False,
        "in_progress": False,
        "demo_doc_count": 0,
        "non_demo_doc_count": 0,
        "last_loaded_at": None,
        "last_error": None,
    }


# ─── /admin/demo/load — happy path on empty backend ──────────────────


def test_load_returns_202_and_flips_in_progress(
    client: tuple[TestClient, object],
    patched_loader: list[tuple],
) -> None:
    """A load on an empty catalog returns 202 + in_progress=True."""
    test_client, _ = client

    response = test_client.post("/admin/demo/load", json={})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["in_progress"] is True
    assert body["demo_doc_count"] == 0
    assert body["non_demo_doc_count"] == 0
    assert body["last_error"] is None

    # Wait for the worker thread so we don't leave dangling state
    # between tests, then confirm the patched loader was invoked
    # against the TestClient's resolved base URL.
    _join_loader_threads()
    assert len(patched_loader) == 1
    argv = patched_loader[0]
    assert argv[0] == "--api"
    assert argv[1].startswith("http://")


# ─── Conflict guard — non-demo doc already present ───────────────────


def test_load_refuses_409_when_non_demo_doc_present(
    client: tuple[TestClient, object],
) -> None:
    """Without ``force=true``, the guard refuses with DEMO_CONFLICT + 409."""
    test_client, services = client
    _seed_document(services, "doc-non-demo", "operator_notes.txt")

    response = test_client.post("/admin/demo/load", json={"force": False})

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "DEMO_CONFLICT"
    # The DemoConflictDetail-shaped detail surfaces the count so the
    # frontend can render "1 non-demo document already present".
    detail = body["detail"]
    assert detail["code"] == "DEMO_CONFLICT"
    assert detail["non_demo_doc_count"] == 1


def test_load_with_force_true_overrides_guard(
    client: tuple[TestClient, object],
    patched_loader: list[tuple],
) -> None:
    """``force=true`` ignores the non-demo guard and proceeds with 202."""
    test_client, services = client
    _seed_document(services, "doc-non-demo", "operator_notes.txt")

    response = test_client.post("/admin/demo/load", json={"force": True})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["in_progress"] is True
    assert body["non_demo_doc_count"] == 1

    _join_loader_threads()
    assert len(patched_loader) == 1


# ─── /admin/demo/reset — soft-archives demo rows only ────────────────


def test_reset_archives_demo_rows_and_leaves_non_demo_untouched(
    client: tuple[TestClient, object],
) -> None:
    """Reset flips archived_at on demo-named rows only.

    Seeds two demo-named documents and one non-demo document, then
    asserts the demo rows acquire ``archived_at`` while the non-demo
    row stays active.
    """
    test_client, services = client
    # Pick two filenames the loader actually produces so the membership
    # check fires positively. The set is computed dynamically from the
    # loader's constants so any rename of the fixture lands here too.
    demo_filenames = sorted(DEMO_FIXTURE_FILENAMES)
    assert len(demo_filenames) >= 2, "demo fixture set unexpectedly small"
    demo_a, demo_b = demo_filenames[0], demo_filenames[1]

    _seed_document(services, "doc-demo-a", demo_a)
    _seed_document(services, "doc-demo-b", demo_b)
    _seed_document(services, "doc-non-demo", "operator_notes.txt")

    response = test_client.post("/admin/demo/reset")

    assert response.status_code == 200, response.text
    catalog = services.documents.catalog
    # Demo rows should be archived; the standard read path hides them.
    assert catalog.get_document("doc-demo-a") is None
    assert catalog.get_document("doc-demo-b") is None
    # Non-demo row should still be visible on the standard read path.
    non_demo = catalog.get_document("doc-non-demo")
    assert non_demo is not None
    assert non_demo.archived_at is None

    # The post-reset status reflects the catalog state: demo count
    # zeroed (archived rows are hidden), non-demo count unchanged.
    body = response.json()
    assert body["demo_doc_count"] == 0
    assert body["non_demo_doc_count"] == 1
    assert body["loaded"] is False
