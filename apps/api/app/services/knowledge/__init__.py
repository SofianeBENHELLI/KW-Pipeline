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

from app.services.knowledge.chat_service import (
    DEFAULT_MAX_OUTPUT_TOKENS as DEFAULT_CHAT_MAX_OUTPUT_TOKENS,
)
from app.services.knowledge.chat_service import (
    KnowledgeChatService,
)
from app.services.knowledge.chunk_relations import (
    ChunkRecord,
    ChunkRelation,
    ChunkRelationKind,
    ChunkRelationService,
)
from app.services.knowledge.embedding_client import (
    EmbeddingClient,
    FakeEmbeddingClient,
    VoyageEmbeddingClient,
)
from app.services.knowledge.entity_extractor import EntityExtractor
from app.services.knowledge.graph_store import (
    ChunkSearchHit,
    GraphStore,
    InMemoryGraphStore,
    Neo4jGraphStore,
)
from app.services.knowledge.llm_client import (
    AnthropicLLMClient,
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
)
from app.services.knowledge.projector import KnowledgeProjector
from app.services.knowledge.reconciliation import (
    DriftedVersion,
    KnowledgeLayerDisabled,
    ReconciliationOutcome,
    ReconciliationService,
)
from app.services.knowledge.neighborhood import (
    KnowledgeNeighborhoodService,
    NeighborhoodNotFound,
)
from app.services.knowledge.relations import (
    KnowledgeRelationsService,
    RelationNotFound,
)
from app.services.knowledge.search import KnowledgeSearchService
from app.services.knowledge.topic_clustering import (
    Topic,
    TopicAssignment,
    TopicClusteringService,
    TopicMembership,
)

__all__ = [
    "AnthropicLLMClient",
    "ChunkRecord",
    "ChunkRelation",
    "ChunkRelationKind",
    "ChunkRelationService",
    "ChunkSearchHit",
    "DEFAULT_CHAT_MAX_OUTPUT_TOKENS",
    "DriftedVersion",
    "EmbeddingClient",
    "EntityExtractor",
    "FakeEmbeddingClient",
    "FakeLLMClient",
    "GeminiLLMClient",
    "GraphStore",
    "InMemoryGraphStore",
    "KnowledgeChatService",
    "KnowledgeLayerDisabled",
    "KnowledgeNeighborhoodService",
    "KnowledgeProjector",
    "KnowledgeRelationsService",
    "KnowledgeSearchService",
    "NeighborhoodNotFound",
    "RelationNotFound",
    "LLMClient",
    "Neo4jGraphStore",
    "ReconciliationOutcome",
    "ReconciliationService",
    "Topic",
    "TopicAssignment",
    "TopicClusteringService",
    "TopicMembership",
    "VoyageEmbeddingClient",
]
