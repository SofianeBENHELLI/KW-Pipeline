"""Pydantic schemas for the operator-imposed taxonomy (ADR-017).

The taxonomy is a tree of categories, each carrying a free-text
``description`` that the embedding-based classifier (B3) reads at
classification time. Categories nest recursively. Ids are stable
(``hr``, ``hr.hybrid_work``) so a re-classify after an edit can
diff outcomes deterministically.

The wire shape lives here because :class:`TaxonomyResponse` is the
``GET /knowledge/taxonomy`` response model (PR B2). The YAML loader
in :mod:`app.services.taxonomy_loader` produces an instance of
:class:`Taxonomy` from disk; the route wraps it in the response
envelope.

ADR-017 calls out tree shape (§3) over flat / graph: trees match
how operators describe a metier ontology and what the Explorer's
left rail already renders.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas import APISchemaModel as BaseModel

# Bumped when the wire shape of the taxonomy changes. The YAML
# loader rejects payloads with an unrecognised version so a v0.2
# rollout never silently parses against v0.1 readers.
TAXONOMY_SCHEMA_VERSION = "v0.1"

# Hard ceiling on nesting depth. Five levels covers the metier
# stories we've heard (department → area → topic → sub-topic →
# detail). Anything deeper is an authoring smell.
MAX_TAXONOMY_DEPTH = 5

# Cap on number of categories at any node. Keeps the YAML file
# legible and the embedding pre-computation bounded for the
# classifier (B3). 256 is well above any realistic operator
# taxonomy.
MAX_TAXONOMY_FANOUT = 256


class TaxonomyCategory(BaseModel):
    """One node in the taxonomy tree.

    ``id`` is stable across runs and is what the classifier writes
    onto chunks/documents (``taxonomy_category_id``). The convention
    is dot-separated lower-snake (``hr.hybrid_work``); the id format
    is enforced at load time, not by this schema, so the wire shape
    stays permissive enough to describe a future v0.2 with different
    rules.

    ``description`` is a free-text paragraph the classifier embeds
    once at taxonomy-publish time and compares against chunk
    embeddings via cosine similarity. Operators write this with the
    metier vocabulary they want to match.

    ``source`` records which half of the hybrid taxonomy this
    category came from (#249, ADR-017): ``"imposed"`` for nodes
    parsed out of the operator-authored YAML, ``"computed"`` for
    nodes auto-deduced from topic clustering on the corpus. The
    field defaults to ``"imposed"`` because the YAML loader is the
    dominant call site and that path always sets it; the route
    layer overrides to ``"computed"`` when synthesising entries
    from the topic-clustering output.
    """

    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    subcategories: list[TaxonomyCategory] = Field(default_factory=list)
    source: Literal["computed", "imposed"] = "imposed"

    @model_validator(mode="after")
    def _check_fanout(self) -> TaxonomyCategory:
        if len(self.subcategories) > MAX_TAXONOMY_FANOUT:
            raise ValueError(
                f"Category {self.id!r}: subcategories must be <= "
                f"{MAX_TAXONOMY_FANOUT}; got {len(self.subcategories)}"
            )
        return self


# Re-build to resolve the recursive forward reference.
TaxonomyCategory.model_rebuild()


class Taxonomy(BaseModel):
    """Top-level taxonomy document.

    Empty ``categories`` is a valid shape — it means the operator
    has the YAML file present but hasn't authored anything yet.
    The ``GET /knowledge/taxonomy`` route distinguishes this from
    "no file at all" via the ``is_configured`` field on the
    response wrapper.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    categories: list[TaxonomyCategory] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_top_level_fanout(self) -> Taxonomy:
        if len(self.categories) > MAX_TAXONOMY_FANOUT:
            raise ValueError(
                f"Top-level categories must be <= {MAX_TAXONOMY_FANOUT}; got {len(self.categories)}"
            )
        return self


class TaxonomyResponse(BaseModel):
    """Response shape for ``GET /knowledge/taxonomy``.

    ``is_configured`` is ``False`` when no YAML file was found at
    the configured path. The frontend uses it to decide whether to
    render the auto-deduction-only state or the imposed taxonomy
    tree. ``source_path`` carries the resolved absolute path when
    configured, ``None`` otherwise — useful for operator debugging.

    The route never returns 404: a missing taxonomy is a valid
    deployment state, not an error.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    is_configured: bool
    source_path: str | None = None
    categories: list[TaxonomyCategory] = Field(default_factory=list)


class TaxonomyImportYamlRequest(BaseModel):
    """Request body for ``POST /admin/taxonomy/import_yaml`` (#379).

    ``path`` is optional — when omitted, the import reads from the
    server-side ``KW_TAXONOMY_PATH`` setting. When provided, it is
    read **on the server** at the supplied path; this field is for
    operators who keep multiple taxonomy YAMLs alongside the data
    directory and want to point the importer at a specific one
    without restarting the API.
    """

    path: str | None = Field(default=None, max_length=4096)


class TaxonomyImportYamlResponse(BaseModel):
    """Response body for ``POST /admin/taxonomy/import_yaml`` (#379).

    Returns the new ``taxonomy_id`` so the operator can correlate
    with the audit event (``orbital.taxonomy.publish``) and the
    fields a dashboard would surface (count of categories, source
    path actually read).
    """

    taxonomy_id: str
    source: Literal["yaml_import"]
    source_path: str
    category_count: int


__all__ = [
    "MAX_TAXONOMY_DEPTH",
    "MAX_TAXONOMY_FANOUT",
    "TAXONOMY_SCHEMA_VERSION",
    "Taxonomy",
    "TaxonomyCategory",
    "TaxonomyImportYamlRequest",
    "TaxonomyImportYamlResponse",
    "TaxonomyResponse",
]
