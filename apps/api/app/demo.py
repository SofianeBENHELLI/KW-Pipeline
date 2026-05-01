"""``kw-demo`` console-script entry point (issue #130).

Wraps ``uvicorn app.main:app`` with the env-var defaults a presenter
needs for a one-paste local demo:

* ``KW_PERSISTENT=true`` — flip the module-level ``app`` to the SQLite
  + filesystem wiring so uploads survive restarts.
* ``KW_CORS_ALLOWED_ORIGINS=http://localhost:5173`` — let the Vite
  dev server (``apps/web``) reach the API.
* ``KW_ALLOWED_CONTENT_TYPES`` — set to the comma-separated allowlist
  ``text/plain,application/pdf,<docx-mime>`` so the demo dataset (text,
  PDF, DOCX) is accepted without the operator pre-configuring it.
  ``<docx-mime>`` is
  ``application/vnd.openxmlformats-officedocument.wordprocessingml.document``.

Each value is set via ``os.environ.setdefault`` so a caller who
already exported one of these is *not* overridden — useful when
running against a non-default port or a cloud frontend.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``kw-demo`` console script."""
    import os

    import uvicorn

    os.environ.setdefault("KW_PERSISTENT", "true")
    os.environ.setdefault("KW_CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    os.environ.setdefault(
        "KW_ALLOWED_CONTENT_TYPES",
        "text/plain,application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
