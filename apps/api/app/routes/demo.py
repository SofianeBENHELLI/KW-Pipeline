"""Transitional Demo-toggle endpoints.

Exposes ``/admin/demo/{load,status,reset}`` so the operator-facing
"Demo" toggle in both front-ends (``apps/explorer`` and ``apps/web``)
can drive the bundled demo loader without dropping into a terminal.

The whole feature lives in three modules:

- :mod:`app.schemas.demo` — request / response shapes.
- :mod:`app.services.demo_dataset` — catalog + state-file primitives.
- :mod:`app.routes.demo` (this module) — HTTP surface.

The transitional posture is intentional: the toggle ships now to make
operator-driven demos easier and is expected to be deleted once a
permanent demo workflow lands. The three module split keeps the
delete a single ``git rm`` per file plus the wire-up unwind in
:mod:`app.routes.__init__`.

Defence-in-depth posture: the existing demo backend already trusts
its own operator (no ``?confirm=true`` gate, no role gate beyond
what every other ``/admin/*`` route inherits from the configured
auth service), and per the brief we keep that posture for the
toggle. Adding ``?confirm=true`` here would diverge from the rest of
the demo surface for no operator benefit.

Resolution of the loader's API base URL: the loader script speaks
HTTP to its own backend, so we read the FastAPI :class:`Request` and
hand the loader ``f"{scheme}://{netloc}"``. The loader then re-uses
its own ``httpx.Client(base_url=...)`` which is exactly the shape
the tests already drive.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status

from app.dependencies import PipelineServices
from app.schemas.demo import DemoLoadRequest, DemoStatusResponse
from app.services.demo_dataset import DemoDatasetService

log = logging.getLogger(__name__)


def build_demo_router(services: PipelineServices) -> APIRouter:
    """Register the Demo-toggle sub-router (transitional, ADR-pending).

    ``services`` is captured by closure so the route handlers can
    reach the catalog store and the configured ``data_dir``. The
    :class:`DemoDatasetService` is constructed once per router build
    rather than per request — its only state is the catalog reference
    and the data-dir path (state-file I/O happens inside method
    bodies under the module-level lock).
    """
    router = APIRouter()

    # Resolve once per router build so the data_dir / catalog wiring
    # is identical across requests. Settings are read via the
    # ``services.settings`` snapshot the dependency container was
    # built with — that's the same posture every other admin route
    # uses for non-secret config.
    demo_service = DemoDatasetService(
        catalog_store=services.documents.catalog,
        data_dir=services.settings.data_dir,
    )

    @router.post(
        "/admin/demo/load",
        operation_id="demo_load",
        response_model=DemoStatusResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def demo_load(body: DemoLoadRequest, request: Request) -> DemoStatusResponse:
        """Kick off the bundled demo loader in a background thread.

        Returns ``202 Accepted`` with the post-start
        :class:`DemoStatusResponse` so the frontend's toggle can flip
        to "Loading…" without a second round-trip to ``/status``. On
        conflict (a load already running, or non-demo docs already
        in the catalog without ``force=true``) the service raises
        :class:`ApiError` with ``DEMO_CONFLICT`` and the global
        handler maps it to ``409`` with the error envelope plus the
        :class:`DemoConflictDetail`-shaped ``detail`` payload the
        frontend reads to render "X non-demo documents already
        present".
        """
        api_base_url = f"{request.url.scheme}://{request.url.netloc}"
        return demo_service.start_load(api_base_url, force=body.force)

    @router.get(
        "/admin/demo/status",
        operation_id="demo_status",
        response_model=DemoStatusResponse,
    )
    def demo_status() -> DemoStatusResponse:
        """Return the current demo-toggle snapshot.

        The frontend polls this every ~2 s while ``in_progress`` is
        true (the loader takes 30-60 s for the full corpus) and stops
        polling once the flag flips back to false. ``demo_doc_count``
        / ``non_demo_doc_count`` come from the catalog; the lifecycle
        flags come from the JSON state file under ``data_dir`` —
        a missing or corrupt file collapses to a fresh-state response
        rather than raising.
        """
        return demo_service.get_status()

    @router.post(
        "/admin/demo/reset",
        operation_id="demo_reset",
        response_model=DemoStatusResponse,
    )
    def demo_reset() -> DemoStatusResponse:
        """Soft-archive every demo-named document and clear the toggle state.

        Flips ``documents.archived_at`` on every catalog row whose
        ``original_filename`` is a bundled demo fixture, then deletes
        the JSON state file. Per the no-delete policy: bytes,
        extractions, semantic JSON, and Markdown remain on disk — an
        operator can chain ``/admin/archive/purge_artifacts`` for a
        hard purge if they want. Already-archived rows are left
        untouched (the catalog primitive is idempotent on archive
        flag).
        """
        return demo_service.reset()

    return router
