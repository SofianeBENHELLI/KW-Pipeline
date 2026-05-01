"""Logging configuration for the Harvester API (issue #42).

Two output shapes are supported, selected by
:attr:`app.settings.Settings.log_format`:

* ``"text"`` — stdlib's default human-readable formatter. Used for
  local development; tracebacks stay multiline and unescaped.
* ``"json"`` — one JSON object per log record, written to stdout.
  Used in container deployments where the log scraper indexes by
  field name. Each record carries ``timestamp``, ``level``,
  ``logger``, ``event`` (the message string), and any keyword
  arguments passed via ``extra={...}``.

Stdlib ``logging`` only — no ``structlog`` / ``python-json-logger``
dependency. The audit-trail call sites in
:mod:`app.services.document_service`, :mod:`app.services.extraction_job_service`,
:mod:`app.services.semantic_output_service`, :mod:`app.routes` and
the knowledge-layer projector all funnel through the standard
``log.info("event.name", extra={...})`` shape so a single formatter
can render them either way.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.settings import Settings

# Standard ``LogRecord`` attributes that should NOT be re-emitted as
# part of the JSON object's "extra" payload — they are either rendered
# explicitly (timestamp, level, logger, event/message) or carry stdlib
# bookkeeping (filename, funcName, …) that would clutter the line.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one JSON object per line.

    The record's message becomes the ``event`` key — call sites
    already use stable, dotted event names like
    ``knowledge.projection.written``. Any keyword arguments passed via
    ``extra={...}`` are merged at the top level so on-call greppers
    can filter on ``document_id`` / ``version_id`` / ``from`` /
    ``to`` without parsing nested objects.
    """

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(
            timespec="milliseconds"
        )
        # ``isoformat`` renders ``+00:00`` for UTC; the canonical
        # production shape is ``Z``-suffixed so log-scraper regexes
        # written against ISO 8601 zulu format match cleanly.
        if timestamp.endswith("+00:00"):
            timestamp = timestamp[: -len("+00:00")] + "Z"
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = _coerce_jsonable(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def _coerce_jsonable(value: Any) -> Any:
    """Best-effort conversion of an ``extra`` value to a JSON-safe shape.

    The :func:`json.dumps` ``default=str`` fallback already handles
    most exotic types, but pre-flattening common containers keeps the
    output readable when logs are eyeballed during incident response.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _coerce_jsonable(v) for k, v in value.items()}
    return str(value)


# Marker attribute used to identify the handler we installed, so
# subsequent calls to :func:`configure_logging` can remove only their
# own previous handler — not pytest's caplog handler, not uvicorn's
# access handler, not anything else a test or framework attached.
_HARVESTER_HANDLER_FLAG = "_harvester_log_handler"


def configure_logging(settings: Settings) -> None:
    """Install a single Harvester root handler matching ``settings.log_format``.

    Idempotent: a previous Harvester handler installed by an earlier
    call is removed first, so building many ``create_app`` instances in
    the same process does not stack duplicate handlers. Foreign
    handlers (pytest's caplog, uvicorn's, anything else attached
    elsewhere) are left in place — replacing the *whole* root handler
    list would break logging-capture in test suites and silently strip
    operator-configured handlers in production.

    The level is read from ``settings.log_level``. Unrecognised level
    names fall back to ``INFO`` rather than raising — a misconfigured
    environment shouldn't take the API down at startup.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HARVESTER_HANDLER_FLAG, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(stream=sys.stdout)
    setattr(handler, _HARVESTER_HANDLER_FLAG, True)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            )
        )
    root.addHandler(handler)

    level = logging.getLevelName(settings.log_level.upper())
    if not isinstance(level, int):
        level = logging.INFO
    root.setLevel(level)
