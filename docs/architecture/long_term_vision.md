# Long-Term Vision — From Knowledge Graph to Enterprise Design Intelligence

KW Pipeline should first focus on building a governed, source-backed enterprise
knowledge graph. The long-term ambition is broader: turn enterprise knowledge
into a living map of what the company knows, what it is trying to design, what it
assumes, and what it still needs to learn.

C-K theory (Concept-Knowledge theory) is a useful long-term framing for this
ambition. It should not be implemented as the first layer of the product. It
should be treated as a later extension of the knowledge graph, once the core
knowledge pipeline is trusted, navigable, and useful.

## Strategic Positioning

The near-term message should remain pragmatic:

```text
We are building a governed enterprise knowledge graph.
It captures source-backed knowledge, makes it navigable, and prepares it for
GraphRAG, cartography, and future design-intelligence extensions.
```

The long-term message is more ambitious:

```text
We can extend the knowledge graph into an Enterprise Concept-Knowledge Map:
what the company knows, what it imagines, what it assumes, what it must validate,
and what it needs to learn next.
```

## Phase Focus

The implementation focus is **Phase 1, Phase 2, and Phase 3**.

C-K theory belongs to **Phase 4+**. It is important for the architecture vision,
but it must not distract from the near-term delivery path.

```text
Phase 1: Governed Knowledge Graph
Phase 2: GraphRAG and Knowledge Navigation
Phase 3: Knowledge Cartography
Phase 4: C-K Design Intelligence Layer
Phase 5: Enterprise Innovation and Learning Map
```

## Phase 1 — Governed Knowledge Graph

Objective: ingest documents and create a trusted graph of validated knowledge.

Scope:

- document ingestion;
- parsing and semantic extraction;
- source references;
- document versions;
- sections / chunks;
- extracted entities;
- source-backed knowledge claims;
- human review;
- validation and rejection;
- provenance on every graph edge.

Output:

```text
A graph of what the company knows, backed by sources and human validation.
```

Key principle:

```text
Nothing without provenance becomes graph knowledge.
```

## Phase 2 — GraphRAG and Knowledge Navigation

Objective: make the validated knowledge graph usable through search, retrieval,
and navigation.

Scope:

- semantic search;
- graph retrieval;
- hybrid RAG;
- source-grounded answers;
- entity navigation;
- requirement similarity;
- duplicate and near-duplicate detection;
- explainable paths from answers back to sources.

Output:

```text
Users and AI companions can navigate validated enterprise knowledge and answer
questions with source-grounded evidence.
```

Key principle:

```text
The assistant should not only answer; it should show where the answer comes from.
```

## Phase 3 — Knowledge Cartography

Objective: show maps of the company's knowledge domains, gaps, overlaps, and
patterns.

Scope:

- requirement clusters;
- document clusters;
- capability maps;
- product / process / customer maps;
- expert maps;
- knowledge-density maps;
- duplicated or fragmented knowledge detection;
- contradiction and gap detection;
- visual exploration of knowledge domains.

Output:

```text
Users can see where enterprise knowledge is dense, fragmented, duplicated,
missing, contradictory, or strategically important.
```

This is the bridge toward future C-K capabilities. At this stage, the system may
start detecting open questions, assumptions, opportunities, and unresolved needs,
but they should remain basic extracted objects, not yet a full C-K framework.

## Phase 4 — C-K Design Intelligence Layer

Objective: explicitly separate **Knowledge** from **Concepts** and use the graph
to support design reasoning, innovation, and learning.

C-K theory distinguishes two spaces:

- **K-space**: propositions considered known or validated in a given context.
- **C-space**: concepts, hypotheses, possible futures, opportunities, or objects
  that are not yet true or false in the current knowledge base.

In KW Pipeline terms:

```text
K-space = validated enterprise knowledge
C-space = emerging concepts, hypotheses, assumptions, and design alternatives
```

Potential graph nodes:

```text
(:KnowledgeClaim)
(:Concept)
(:Assumption)
(:Question)
(:Experiment)
(:KnowledgeGap)
(:Decision)
(:Requirement)
(:Expert)
(:Product)
(:Process)
(:Capability)
```

Potential relationships:

```text
(:Concept)-[:INSPIRED_BY]->(:KnowledgeClaim)
(:Concept)-[:REQUIRES_KNOWLEDGE]->(:Question)
(:Question)-[:ANSWERED_BY]->(:KnowledgeClaim)
(:Requirement)-[:CONSTRAINS]->(:Concept)
(:Experiment)-[:VALIDATES]->(:Concept)
(:Decision)-[:SELECTS]->(:Concept)
(:Decision)-[:REJECTS]->(:Concept)
(:KnowledgeClaim)-[:CONTRADICTS]->(:KnowledgeClaim)
(:Concept)-[:EXPANDS_TO]->(:Concept)
```

Output:

```text
A graph of what the company knows and what the company is trying to design,
validate, or learn.
```

Key principle:

```text
LLM-generated concepts are not facts. They are candidates that require review,
ownership, and validation.
```

## Phase 5 — Enterprise Innovation and Learning Map

Objective: use the C-K extended graph to steer innovation, learning, and
execution.

Scope:

- concept maturity tracking;
- missing-knowledge backlog;
- expert activation;
- recommended experiments;
- simulation and validation planning;
- innovation portfolio maps;
- strategic capability-gap analysis;
- links to projects, requirements, products, and customer opportunities.

Output:

```text
The company can manage its knowledge, concepts, assumptions, and learning agenda
as a living enterprise system.
```

This moves the platform beyond search and GraphRAG. It becomes an enterprise
intelligence layer for knowledge, innovation, and decision support.

## Architecture Implications to Preserve Now

Even if C-K is deferred to Phase 4+, the current graph model should remain open
to future nodes such as:

- `Concept`;
- `Assumption`;
- `Question`;
- `Experiment`;
- `KnowledgeGap`;
- `Decision`.

The current implementation should avoid hard-coding the graph as only a document
and entity graph. It should preserve enough extensibility to add design-reasoning
objects later.

## What Not to Do Now

- Do not position the near-term product as a C-K platform.
- Do not force every document into C-K categories from day one.
- Do not mix known facts and imagined concepts without explicit status.
- Do not let LLM-generated concepts become validated knowledge automatically.
- Do not overbuild the ontology before the governed knowledge graph and
  cartography layers prove their value.

## Recommended Narrative

Use this wording for near-term communication:

```text
We are first building a governed enterprise knowledge graph and navigation layer.
Then we will extend it into knowledge cartography.
Once the graph is mature, C-K theory can help us go further: mapping not only
what the company knows, but also what it imagines, assumes, needs to validate,
and needs to learn.
```
