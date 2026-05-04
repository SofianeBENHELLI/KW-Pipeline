"""Logging handler that persists structured events to an audit store.

Sits next to :func:`app.logging_config.configure_logging` and is
attached to the root logger when ``KW_AUDIT_ENABLED`` is truthy. Only
records whose ``msg`` looks like a dotted event name (matches
:data:`_EVENT_NAME_PATTERN`) are persisted — plain-prose log lines
("Starting up...", warnings from third-party libraries, etc.) are
ignored so the audit table stays a clean event log.

Failure isolation: a backend hiccup (SQLite locked, disk full) must
not bring down the request that emitted the log. The handler catches
every exception from :meth:`AuditEventStore.append` and re-raises only
to ``logging``'s own error path, which the configured root handler
already prints to stderr.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.services.audit_event_store import AuditEvent, AuditEventStore

# A "domain event" record is one whose ``msg`` is a stable dotted
# identifier — the convention from observability.md. We intentionally
# require at least one dot so plain words ("Starting", "OK") never
# qualify, and we restrict to lower-snake segments to match the
# documented naming policy.
_EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# ``logging.LogRecord`` keeps a fixed set of attributes; anything else
# arrived via ``extra=`` and is therefore part of the audit payload.
# We snapshot the well-known keys so we can subtract them when
# extracting the payload dict.
_LOGRECORD_RESERVED_KEYS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"message", "asctime"}


class AuditLogHandler(logging.Handler):
    """Persist domain-event log records into an :class:`AuditEventStore`.

    Construct with the store the handler should write to. Attach to
    the root logger via ``logging.getLogger().addHandler(handler)``;
    detach with ``removeHandler`` when the app shuts down.

    Levels: by default only ``INFO`` and above flow through, matching
    the standard handler's bar. Lower the handler's own level to
    capture ``DEBUG`` events too.
    """

    def __init__(self, store: AuditEventStore, *, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = _record_to_event(record)
            if event is None:
                return
            self._store.append(event)
        except Exception:  # noqa: BLE001 - logging-handler isolation
            # ``handleError`` writes to stderr by default; never raises.
            self.handleError(record)


def _record_to_event(record: logging.LogRecord) -> AuditEvent | None:
    """Convert a ``LogRecord`` into an :class:`AuditEvent`, or ``None``.

    Returns ``None`` for records whose ``msg`` doesn't match the
    dotted-event-name pattern. The handler skips persisting those so
    the audit table only ever contains the documented vocabulary.
    """
    name = record.msg
    if not isinstance(name, str) or not _EVENT_NAME_PATTERN.match(name):
        return None

    payload = _extract_payload(record)
    document_id = payload.get("document_id")
    version_id = payload.get("version_id")
    return AuditEvent(
        event_name=name,
        level=record.levelname,
        ts_utc=datetime.fromtimestamp(record.created, tz=UTC),
        document_id=document_id if isinstance(document_id, str) else None,
        version_id=version_id if isinstance(version_id, str) else None,
        payload=payload,
    )


def _extract_payload(record: logging.LogRecord) -> dict[str, Any]:
    """Return the user-supplied ``extra`` dict from a log record.

    ``logging`` flattens ``extra=`` into the record's instance dict;
    we subtract the reserved attribute names to recover what the
    emitter passed in.
    """
    return {
        key: value for key, value in vars(record).items() if key not in _LOGRECORD_RESERVED_KEYS
    }


__all__ = ["AuditLogHandler"]
