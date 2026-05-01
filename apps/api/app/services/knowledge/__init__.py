"""Knowledge layer (ADR-012).

Sits on top of validated ``SemanticDocument``s. Phase 1 of the layer
projects documents/versions/sections into a graph and exposes them
via two read-only HTTP endpoints. Phases 2 and 3 add LLM-driven
entity extraction (ADR-013) and a chat surface; both layer behind
the same ``GraphStore`` Protocol introduced here.

Public surface (intentionally small):

- :class:`GraphStore` — Protocol that abstracts the graph backend.
- :class:`InMemoryGraphStore` — deterministic test fake.
- :class:`Neo4jGraphStore` — production implementation; lazy-imports
  the ``neo4j`` driver so unit tests against the fake don't need a
  running database.
- :class:`KnowledgeProjector` — turns a validated document into nodes
  and edges and upserts them through a :class:`GraphStore`.
"""

from app.services.knowledge.entity_extractor import EntityExtractor
from app.services.knowledge.graph_store import (
    GraphStore,
    InMemoryGraphStore,
    Neo4jGraphStore,
)
from app.services.knowledge.llm_client import (
    AnthropicLLMClient,
    FakeLLMClient,
    LLMClient,
)
from app.services.knowledge.projector import KnowledgeProjector

__all__ = [
    "AnthropicLLMClient",
    "EntityExtractor",
    "FakeLLMClient",
    "GraphStore",
    "InMemoryGraphStore",
    "KnowledgeProjector",
    "LLMClient",
    "Neo4jGraphStore",
]
