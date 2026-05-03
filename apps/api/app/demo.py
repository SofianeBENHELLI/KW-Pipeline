"""``kw-demo`` console-script entry point (issue #130).

Wraps ``uvicorn app.main:app`` with the env-var defaults a presenter
needs for a one-paste local demo:

* ``KW_PERSISTENT=true`` — flip the module-level ``app`` to the SQLite
  + filesystem wiring so uploads survive restarts.
* ``KW_CORS_ALLOWED_ORIGINS=http://localhost:5173,https://localhost:8081`` —
  let the Vite dev server (``apps/web``) and the 3DEXPERIENCE widget dev
  server (``apps/widget``, served by webpack-dev-server on 8081) reach
  the API.
* ``KW_ALLOWED_CONTENT_TYPES`` — set to the comma-separated allowlist
  ``text/plain,application/pdf,<docx-mime>,<pptx-mime>`` so the demo
  dataset (text, PDF, DOCX, PPTX) is accepted without the operator
  pre-configuring it. The MIME strings are
  ``application/vnd.openxmlformats-officedocument.wordprocessingml.document``
  for DOCX and
  ``application/vnd.openxmlformats-officedocument.presentationml.presentation``
  for PPTX.
* ``KW_KNOWLEDGE_LAYER_ENABLED=true`` — turn on the v0.2 KG projection
  so validated documents materialise chunks, topics, and deterministic
  semantic relations against the in-memory ``GraphStore``. No Neo4j
  required for the live presenter path; setting ``KW_NEO4J_URI``
  separately switches to the Neo4j-backed store.

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
    os.environ.setdefault(
        "KW_CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,https://localhost:8081",
    )
    os.environ.setdefault(
        "KW_ALLOWED_CONTENT_TYPES",
        "text/plain,application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    os.environ.setdefault("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
