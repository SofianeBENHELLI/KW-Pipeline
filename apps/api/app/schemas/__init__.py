"""Pydantic schemas for API contracts."""

from pydantic import BaseModel, ConfigDict


class APISchemaModel(BaseModel):
    """Base for response-shaped schemas exposed via the OpenAPI contract.

    ``json_schema_serialization_defaults_required=True`` makes fields with
    defaults (e.g. ``Field(default_factory=list)``) appear in the JSON Schema
    ``required`` list when the schema is generated in serialization mode —
    the mode FastAPI uses for response models. The wire contract is then
    honest: a list field that always serializes (possibly empty) is marked
    required, so generated TypeScript clients see ``T[]`` instead of
    ``T[] | undefined``. Validation-mode schemas (request bodies) keep the
    field optional, so callers can still omit it and accept the default.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
