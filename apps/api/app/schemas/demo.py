"""Request / response shapes for the transitional Demo Dataset toggle.

Exposed on ``/admin/demo/*`` and consumed by the "Demo" toggle in both
front-ends (``apps/explorer`` and ``apps/web``). The whole feature is
intentionally isolated in this module + :mod:`app.services.demo_dataset`
+ :mod:`app.routes.demo` so it can be ripped out cleanly when the
transitional period ends.

Three shapes:

* :class:`DemoLoadRequest`   — body of ``POST /admin/demo/load``.
* :class:`DemoStatusResponse` — body of ``GET /admin/demo/status``
  (also returned by ``POST /admin/demo/load`` and
  ``POST /admin/demo/reset`` so the front-end can refresh its toggle
  state without a second round-trip).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel


class DemoLoadRequest(BaseModel):
    """Body for ``POST /admin/demo/load``.

    ``force=False`` (the default) refuses the call with ``409 Conflict``
    if the catalog already contains documents that the demo loader did
    not produce — the conflict guard the operator agreed to in the
    "transitional Demo toggle" thread. ``force=True`` ignores the guard
    and proceeds (used by tests + power-user override).
    """

    force: bool = Field(
        default=False,
        description=(
            "When ``False`` (default), refuses with 409 if any "
            "non-demo document is already present in the catalog. "
            "When ``True``, ignores the conflict guard."
        ),
    )


class DemoStatusResponse(BaseModel):
    """Snapshot of the demo dataset's lifecycle on the running backend.

    The front-end's toggle component polls this every ~2 s while
    ``in_progress`` is true (the loader takes 30-60 s for the 47-version
    full corpus), then stops polling and refreshes the corpus view once
    ``in_progress`` flips back to false.

    Field semantics:

    * ``loaded`` — at least one demo-tagged document is currently in
      the catalog (regardless of whether the load is still running).
    * ``in_progress`` — a load is currently executing in a background
      thread; the toggle should render disabled with a "Loading…" badge.
    * ``demo_doc_count`` — number of catalog rows tagged as demo (i.e.
      whose ``original_filename`` matches one of the bundled fixtures).
      Capped at the fixture count (47 versions across 45 documents) so
      the UI can render "38 / 47".
    * ``non_demo_doc_count`` — number of catalog rows the demo loader
      did **not** produce. Surfaces in the conflict-guard 409 payload
      so the operator knows why the load was refused.
    * ``last_loaded_at`` — wall clock the most recent successful load
      finished. ``None`` until the first successful load.
    * ``last_error`` — error message from the most recent load attempt
      that failed (or was aborted mid-run). ``None`` once a subsequent
      load succeeds.
    """

    loaded: bool
    in_progress: bool
    demo_doc_count: int = Field(ge=0)
    non_demo_doc_count: int = Field(ge=0)
    last_loaded_at: datetime | None = None
    last_error: str | None = None


class DemoConflictDetail(BaseModel):
    """Body returned with 409 when the conflict guard refuses a load.

    Matches the standard :class:`app.errors.ApiError` envelope
    (``code`` + ``detail``) plus a ``non_demo_doc_count`` hint so the
    front-end can render "X non-demo documents already present" without
    a second ``GET /admin/demo/status`` round-trip.
    """

    code: str = "DEMO_CONFLICT"
    detail: str
    non_demo_doc_count: int = Field(ge=0)
