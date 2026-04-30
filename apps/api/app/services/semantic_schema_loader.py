"""Versioned loader for persisted ``SemanticDocument`` payloads.

Per ADR-008, ``SemanticDocument.schema_version`` follows ``vMAJOR.MINOR``.
Old persisted payloads must keep loading after the schema evolves: this
module is the single boundary that dispatches by ``schema_version``,
applies the registered migrator (current versions are identity), and
finally validates the result against the live Pydantic model.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.schemas.semantic_document import SemanticDocument


class UnsupportedSchemaVersion(ValueError):
    """Raised when a persisted payload declares a ``schema_version`` that has
    no registered migrator (typically a future version this build cannot
    yet read)."""


def _identity(payload: dict) -> dict:
    return payload


# Mapping of persisted ``schema_version`` -> migrator producing a current-shape
# ``SemanticDocument`` payload (i.e. one that validates against the live
# Pydantic model). Today only ``v0.1`` exists, so the current code is its own
# migrator (identity). When the schema evolves, add the prior version here
# with a migrator that yields a current-shape payload, plus a fixture and a
# CHANGELOG entry per ADR-008.
MIGRATORS: dict[str, Callable[[dict], dict]] = {
    "v0.1": _identity,
}


def load_semantic_document(payload: dict | str) -> SemanticDocument:
    """Load a persisted SemanticDocument payload through the migrator chain.

    Accepts either a JSON string (as persisted in
    ``semantic_documents.payload``) or a dict already parsed from JSON.
    """
    data = json.loads(payload) if isinstance(payload, str) else dict(payload)

    raw_version = data.get("schema_version")
    migrator = MIGRATORS.get(raw_version)
    if migrator is None:
        raise UnsupportedSchemaVersion(
            f"Unsupported SemanticDocument schema_version: {raw_version!r}. "
            f"Known versions: {sorted(MIGRATORS)}."
        )
    migrated = migrator(data)
    return SemanticDocument.model_validate(migrated)
