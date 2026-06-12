"""Transitional Demo-toggle service — load / status / reset helpers.

Companion to :mod:`app.routes.demo` and :mod:`app.schemas.demo`. The
whole feature is intentionally isolated in these three modules so a
future ``git rm`` removes the surface in one shot once the permanent
demo workflow ships.

Surface contract:

- :data:`DEMO_FIXTURE_FILENAMES` — the canonical set of filenames the
  bundled loader pushes onto the catalog. Computed once at module
  import by reaching into ``apps/api/scripts/load_demo_dataset.py`` via
  the same ``sys.path`` trick :mod:`app.demo_loader` already uses, so a
  fixture rename in the loader is observed here automatically. The set
  also includes the two binary fixtures the loader materialises in
  ``_load_binary_fixtures`` plus the renamed-duplicate filename used to
  exercise the duplicate-detection path.
- :class:`DemoDatasetService` — wraps the :class:`CatalogStore` for the
  three operator actions: ``get_status``, ``start_load``, ``reset``.
  Holds a small JSON state file (``<data_dir>/demo-state.json``) for
  ``in_progress`` / ``last_loaded_at`` / ``last_error``; the catalog is
  the source of truth for the demo / non-demo doc counts.

Concurrency model: a module-level :class:`threading.Lock` serialises
state-file writes and the "is a load already running?" check. The
load itself runs in a daemon background thread that re-uses the
loader script's ``main(argv)`` entry point — that script already
talks to its own backend over HTTP via ``httpx``, so the thread does
not need to share any process state with the running FastAPI worker.

Per the no-delete policy, ``reset`` only flips
``documents.archived_at`` via :meth:`CatalogStore.flag_document_archived`;
hard purge stays out of scope and an operator can chain
``/admin/archive/purge_artifacts`` afterwards if they want bytes gone.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.errors import ApiError, ErrorCode
from app.schemas.demo import DemoConflictDetail, DemoStatusResponse
from app.services.catalog_store import CatalogStore

log = logging.getLogger(__name__)

# Filename the loader writes for the JSON-encoded toggle state. Lives
# under ``data_dir`` next to the SQLite catalog so a persistent demo
# survives backend restarts; the in-memory wiring still works because
# the loader tolerates a missing / corrupt file as "fresh state".
_STATE_FILENAME = "demo-state.json"

# Actor recorded on the soft-archive audit row when ``reset`` flips a
# demo document. The string is chosen to be obvious in the audit log
# so an operator chasing "why did this row get archived?" lands on the
# transitional toggle rather than wondering about a real cascade.
_RESET_ACTOR = "demo-toggle"

# Module-level lock guarding the state-file writes and the
# "is a load already running?" check. A per-instance lock would be
# safer if we expected multiple :class:`DemoDatasetService` instances
# in one process, but we don't (one ``services`` container per
# FastAPI app, and the service is constructed lazily by the router).
_STATE_LOCK = threading.Lock()


def _resolve_demo_fixture_filenames() -> frozenset[str]:
    """Compute the canonical demo-fixture filename set from the loader.

    Reaches into ``apps/api/scripts/load_demo_dataset.py`` via the same
    ``sys.path`` trick :mod:`app.demo_loader` uses (the script lives
    outside the wheel package list per ``pyproject.toml``). We import
    the constants once at module load to avoid re-paying the
    ``sys.path`` mutation on every status call, and intentionally
    accept a hard import failure here — the demo toggle has no useful
    posture without the loader on disk.

    The set is the union of:

    - ``TEXT_FIXTURES`` — every standalone topical document.
    - ``SUPPLIER_FAMILY`` and ``ECU_FAMILY`` — the two multi-version
      families (v1 / v2 / v3 of each), which the loader uploads
      individually.
    - ``engineering_change_request.pdf`` and
      ``weekly_quality_review.docx`` — the two binary fixtures
      :func:`scripts.load_demo_dataset._load_binary_fixtures`
      materialises into ``apps/api/fixtures/full_demo/``.
    - ``supplier_onboarding_policy_v1_renamed.txt`` — the renamed
      duplicate the loader uploads in ``_load_duplicate`` to fire the
      ``DUPLICATE_DETECTED`` path. Same bytes as the v1 fixture but a
      distinct filename, so the catalog row's ``original_filename``
      lands as ``..._renamed.txt``.
    """
    api_root = Path(__file__).resolve().parent.parent.parent
    scripts_dir = api_root / "scripts"
    if not scripts_dir.exists():
        # Same diagnostic as :mod:`app.demo_loader` — the editable install
        # is the supported path for this transitional feature.
        raise SystemExit(
            "Cannot locate the demo loader script directory at "
            f"{scripts_dir}. The Demo toggle requires an editable "
            "install (`pip install -e 'apps/api[test]'`) so the "
            "scripts/ directory is reachable on disk."
        )
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    # ``load_demo_dataset`` lives outside the ``app`` import package so
    # mypy cannot resolve the import at type-check time. The runtime
    # sys.path tweak above takes care of resolution at execution time.
    from load_demo_dataset import (  # type: ignore[import-not-found]  # noqa: PLC0415
        ECU_FAMILY,
        SUPPLIER_FAMILY,
        TEXT_FIXTURES,
    )

    names: set[str] = set()
    names.update(filename for filename, _cluster in TEXT_FIXTURES)
    names.update(filename for filename, _cluster in SUPPLIER_FAMILY)
    names.update(filename for filename, _cluster in ECU_FAMILY)
    # Binary fixtures materialised by the loader at runtime. Their
    # filenames are not in any of the constant lists above because the
    # loader hardcodes them inside ``_load_binary_fixtures``; we mirror
    # those literals here so a rename of either binary updates both
    # call sites in the same change.
    names.add("engineering_change_request.pdf")
    names.add("weekly_quality_review.docx")
    # Renamed duplicate of supplier_onboarding_policy_v1.txt. The
    # loader uploads it under this filename to drive the duplicate-
    # detection path — same bytes as v1 but the catalog row records
    # ``..._renamed.txt`` as ``original_filename``.
    names.add("supplier_onboarding_policy_v1_renamed.txt")
    return frozenset(names)


DEMO_FIXTURE_FILENAMES: frozenset[str] = _resolve_demo_fixture_filenames()


class DemoDatasetService:
    """Operator-facing wrapper around the bundled demo loader.

    Three actions exist:

    - :meth:`get_status` — count demo / non-demo rows in the catalog
      and read the lifecycle JSON state file.
    - :meth:`start_load` — check the conflict guard, write
      ``in_progress=true``, spawn a background thread that runs the
      loader's ``main(argv)`` against ``api_base_url``.
    - :meth:`reset` — soft-archive every demo-named row via
      :meth:`CatalogStore.flag_document_archived` and clear the JSON
      state file.

    The catalog is the source of truth for "is the demo loaded?". The
    JSON state file only carries lifecycle hints (``in_progress`` /
    ``last_loaded_at`` / ``last_error``) so a backend restart resumes
    cleanly — and a missing or corrupt file is treated as fresh state
    (see :meth:`_read_state`).
    """

    def __init__(self, *, catalog_store: CatalogStore, data_dir: Path | str) -> None:
        self._catalog = catalog_store
        # Resolve eagerly so ``Path`` semantics (``mkdir`` parents,
        # ``__truediv__`` joins) are stable for the rest of the
        # service. The directory may not exist yet on a fresh demo
        # boot — we create it lazily on the first state write.
        self._data_dir = Path(data_dir)

    # ─── Public API ──────────────────────────────────────────────

    def get_status(self) -> DemoStatusResponse:
        """Return the current demo-toggle snapshot.

        Walks the catalog with the standard ``list_documents`` cursor
        protocol (``cursor`` token + ``limit`` page) and partitions
        rows into demo / non-demo by ``original_filename``. Reads the
        JSON state file for the in-progress flag and the last-load /
        last-error timestamps; a missing or corrupt file collapses to
        a fresh-state response.
        """
        demo_count, non_demo_count = self._count_documents()
        state = self._read_state()
        return DemoStatusResponse(
            loaded=demo_count > 0,
            in_progress=bool(state.get("in_progress", False)),
            demo_doc_count=demo_count,
            non_demo_doc_count=non_demo_count,
            last_loaded_at=_parse_iso(state.get("last_loaded_at")),
            last_error=state.get("last_error"),
        )

    def start_load(
        self,
        api_base_url: str,
        *,
        force: bool,
    ) -> DemoStatusResponse:
        """Spawn the loader in a background thread.

        Conflict-guard rules:

        - If a load is already in flight (``in_progress=true`` in the
          state file) we refuse with the same ``DEMO_CONFLICT`` 409
          shape the non-demo-doc guard uses. Concurrent toggles are an
          operator error, not a race we want to silently coalesce.
        - If ``force=False`` and the catalog has any non-demo rows we
          refuse with ``DEMO_CONFLICT`` and surface the count so the
          frontend can render "X non-demo documents already present".
        - With ``force=True`` we skip the non-demo guard and let the
          loader run on top of whatever is already in the catalog —
          the loader's own duplicate-detection logic handles re-uploads
          gracefully.

        Side effects on the happy path: write
        ``in_progress=true`` + clear ``last_error`` to the state file,
        spawn a daemon ``threading.Thread`` running
        ``scripts.load_demo_dataset.main(['--api', api_base_url])``,
        and return the post-start status.
        """
        with _STATE_LOCK:
            state = self._read_state()
            if state.get("in_progress"):
                # 409 instead of "join the existing load" because the
                # transitional toggle has no UX surface to merge two
                # operator clicks; refusing is the safe default.
                raise ApiError(
                    status_code=409,
                    code=ErrorCode.DEMO_CONFLICT,
                    message=(
                        "A demo dataset load is already in progress; "
                        "wait for it to finish or call /admin/demo/reset."
                    ),
                    retryable=True,
                    remediation=(
                        "Poll GET /admin/demo/status until "
                        "in_progress=false, then re-issue the load."
                    ),
                    detail=DemoConflictDetail(
                        detail=("A demo dataset load is already in progress."),
                        non_demo_doc_count=self._count_documents()[1],
                    ).model_dump(),
                )

            demo_count, non_demo_count = self._count_documents()
            if not force and non_demo_count > 0:
                raise ApiError(
                    status_code=409,
                    code=ErrorCode.DEMO_CONFLICT,
                    message=(
                        f"Catalog already contains {non_demo_count} "
                        "non-demo document(s); refuse the demo load to "
                        "avoid clobbering operator data."
                    ),
                    retryable=False,
                    remediation=(
                        "Re-issue with force=true to ignore the guard, "
                        "or archive the non-demo documents first."
                    ),
                    detail=DemoConflictDetail(
                        detail=(f"Catalog already contains {non_demo_count} non-demo document(s)."),
                        non_demo_doc_count=non_demo_count,
                    ).model_dump(),
                )

            # Stamp the state file before we spawn the worker so the
            # status route returns ``in_progress=true`` to the very
            # next poll, even if the OS scheduler holds the worker
            # off for a moment.
            new_state: dict[str, Any] = {
                "in_progress": True,
                "last_loaded_at": state.get("last_loaded_at"),
                "last_error": None,
            }
            self._write_state(new_state)

        thread = threading.Thread(
            target=self._run_loader,
            kwargs={"api_base_url": api_base_url},
            name="demo-toggle-loader",
            daemon=True,
        )
        thread.start()

        # Return the post-start snapshot directly — counts haven't
        # changed yet (the loader hasn't uploaded anything), but the
        # in_progress flag is the bit the frontend cares about.
        return DemoStatusResponse(
            loaded=demo_count > 0,
            in_progress=True,
            demo_doc_count=demo_count,
            non_demo_doc_count=non_demo_count,
            last_loaded_at=_parse_iso(new_state.get("last_loaded_at")),
            last_error=None,
        )

    def reset(self) -> DemoStatusResponse:
        """Soft-archive every demo-named row and clear the JSON state.

        Iterates the catalog once and calls
        :meth:`CatalogStore.flag_document_archived` on each row whose
        ``original_filename`` is a demo fixture. The catalog primitive
        is idempotent — re-archiving an already-archived row preserves
        the original ``archived_at`` — so calling reset twice is a
        no-op on the second pass.

        Per the no-delete policy: bytes / extractions / semantic JSON
        / Markdown stay on disk. The operator can chain
        ``/admin/archive/purge_artifacts`` after this call if they
        want the bytes gone too. The KG subgraph is left to the
        existing archive cascade (out of scope for the toggle).
        """
        # Re-stamp provenance before archiving so rows left behind by a
        # crashed mid-flight load (which never reached the post-load
        # stamp) still get tagged ``origin='demo'`` on their way out.
        self._catalog.mark_documents_origin(DEMO_FIXTURE_FILENAMES, origin="demo")

        archived_at = datetime.now(UTC)
        for document in self._iter_documents(include_archived=False):
            if document.original_filename in DEMO_FIXTURE_FILENAMES:
                try:
                    self._catalog.flag_document_archived(
                        document.id,
                        archived_at=archived_at,
                        actor=_RESET_ACTOR,
                    )
                except KeyError:
                    # Race against another writer that already removed
                    # the row — treat as already-handled and continue.
                    log.info(
                        "demo_dataset.reset.row_disappeared",
                        extra={"document_id": document.id},
                    )

        with _STATE_LOCK:
            self._clear_state_file()

        return self.get_status()

    # ─── Internals ───────────────────────────────────────────────

    def _count_documents(self) -> tuple[int, int]:
        """Return ``(demo_count, non_demo_count)`` over active rows.

        Walks the catalog page-by-page using the ``cursor`` protocol
        documented on :meth:`CatalogStore.list_documents`. Archived
        rows (``archived_at IS NOT NULL``) are filtered out of the
        standard read path by the catalog itself, so reset-then-
        reload-poll converges to ``demo_doc_count=0`` once the toggle
        finishes archiving.
        """
        demo = 0
        non_demo = 0
        for document in self._iter_documents(include_archived=False):
            if document.original_filename in DEMO_FIXTURE_FILENAMES:
                demo += 1
            else:
                non_demo += 1
        return demo, non_demo

    def _iter_documents(self, *, include_archived: bool):  # type: ignore[no-untyped-def]
        """Yield every active catalog document, paginating as needed.

        The catalog's :meth:`list_documents` honours ``limit`` per
        call but does not return a next-page token — the cursor for
        the next page is the last row's ``(created_at, id)``. We use
        a fixed page size and re-encode the cursor by reading the
        last row's fields off the returned :class:`Document` objects.

        ``include_archived`` is plumbed for forward-compat; the demo
        toggle today always wants the active read path so archived
        rows from a prior reset don't double-count when the operator
        re-loads.
        """
        from app.services.catalog_store import _encode_cursor  # noqa: PLC0415

        del include_archived  # see docstring; the standard read path
        # already filters archived rows. A future variant that also
        # surfaces archived demo docs would flip a flag here.
        page_size = 100
        cursor: str | None = None
        while True:
            page = self._catalog.list_documents(cursor=cursor, limit=page_size)
            if not page:
                return
            yield from page
            if len(page) < page_size:
                return
            last = page[-1]
            cursor = _encode_cursor((last.created_at, last.id))

    def _run_loader(self, *, api_base_url: str) -> None:
        """Background-thread entry point: invoke the loader's ``main``.

        On success, stamp ``last_loaded_at`` and clear ``last_error``;
        on any exception, stamp ``last_error`` with a truncated
        message. ``in_progress`` is always cleared before the thread
        exits so a crashed loader does not wedge the toggle in the
        loading state.
        """
        api_root = Path(__file__).resolve().parent.parent.parent
        scripts_dir = api_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        try:
            # ``load_demo_dataset`` lives outside the ``app`` import
            # package; ``_resolve_demo_fixture_filenames`` (called at
            # module load) has already inserted ``scripts/`` into
            # ``sys.path``, so the import here resolves cleanly. The
            # local re-import avoids importing ``runner`` at module
            # import time and keeps the loader optional for callers
            # that never actually trigger a load.
            from load_demo_dataset import main as runner  # noqa: PLC0415

            rc = int(runner(["--api", api_base_url]) or 0)
            if rc != 0:
                raise RuntimeError(f"demo loader exited with rc={rc}")
        except Exception as exc:  # noqa: BLE001 — funnel any failure
            # back into the operator-visible state file so the toggle
            # can render the error instead of spinning forever.
            log.exception("demo_dataset.load.failed")
            with _STATE_LOCK:
                state = self._read_state()
                state["in_progress"] = False
                state["last_error"] = _truncate(str(exc), limit=500)
                self._write_state(state)
            return

        # Stamp provenance on the freshly-loaded rows (Explorer Sprint
        # 1). The loader uploads through the public HTTP route, which
        # always writes ``origin='operator'`` via the column default —
        # the post-load stamp by fixture filename is what makes demo
        # rows first-class distinguishable on every read surface.
        try:
            stamped = self._catalog.mark_documents_origin(DEMO_FIXTURE_FILENAMES, origin="demo")
            log.info("demo_dataset.load.origin_stamped", extra={"rows": stamped})
        except Exception:  # noqa: BLE001 — stamping must not wedge the toggle
            log.exception("demo_dataset.load.origin_stamp_failed")

        with _STATE_LOCK:
            state = self._read_state()
            state["in_progress"] = False
            state["last_loaded_at"] = datetime.now(UTC).isoformat()
            state["last_error"] = None
            self._write_state(state)

    def _state_path(self) -> Path:
        return self._data_dir / _STATE_FILENAME

    def _read_state(self) -> dict[str, Any]:
        """Load the JSON state file or return an empty dict.

        Tolerant of a missing file (fresh boot), corrupt JSON
        (operator hand-edited), and unexpected top-level shapes
        (anything that's not a dict). Each of those collapses to "no
        prior state" — the catalog still owns the truth on whether
        the demo is loaded.
        """
        path = self._state_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning(
                "demo_dataset.state.read_failed",
                extra={"path": str(path), "error": str(exc)},
            )
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning(
                "demo_dataset.state.corrupt",
                extra={"path": str(path), "error": str(exc)},
            )
            return {}
        if not isinstance(payload, dict):
            return {}
        return dict(payload)

    def _write_state(self, payload: dict[str, Any]) -> None:
        """Persist the state dict to ``demo-state.json``.

        Caller is responsible for holding :data:`_STATE_LOCK` so the
        write is serialised across the route + worker threads. The
        directory is created on first write so a fresh in-memory
        wiring (no SQLite catalog → no preexisting ``data_dir``) is
        still able to record state.
        """
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _clear_state_file(self) -> None:
        """Remove the JSON state file, tolerating a missing target.

        Caller holds :data:`_STATE_LOCK`. We delete instead of
        overwriting with an empty dict so the on-disk surface stays
        small and the next ``_read_state`` short-circuits on
        ``FileNotFoundError`` rather than re-parsing JSON every poll.
        """
        path = self._state_path()
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning(
                "demo_dataset.state.unlink_failed",
                extra={"path": str(path), "error": str(exc)},
            )


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort parse of an ISO-8601 timestamp from the state file.

    Tolerant of every malformed shape (non-string, missing key, junk
    text) — the toggle's contract is "if we can't read it, treat it
    as never set" rather than raising on read.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _truncate(text: str, *, limit: int) -> str:
    """Cap a string to ``limit`` characters with an ellipsis suffix."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
