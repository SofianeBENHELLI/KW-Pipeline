/**
 * Focused graph lens — renders a bounded `/knowledge/neighborhood`
 * payload via the same NVL canvas the per-document view uses.
 *
 * ADR-028 §"Information Architecture" §3 forbids defaulting to the
 * full-corpus render — this component MUST consume only the
 * neighborhood payload. The component never falls back to
 * `GET /knowledge/graph` (corpus-scale).
 *
 * Heavy dependencies (`@neo4j-nvl/base`, `@neo4j-nvl/react`) are dragged
 * in by importing `./FocusedLensCanvas`, so this module is lazy-loaded
 * by `TopicDetailView` via `React.lazy()` to keep the initial Explorer
 * chunk small.
 */

import { useEffect, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import type { Relationship } from "@neo4j-nvl/base";

import { ApiError, getKnowledgeNeighborhood } from "../../api/client";
import type {
  ApiGraphEdge,
  ApiKnowledgeNeighborhood,
  ApiKnowledgeNeighborhoodEdge,
} from "../../api/types";
import { toNvlNodes, toNvlRelationships } from "../graph/types";

interface FocusedLensProps {
  rootKind: "document" | "topic" | "chunk";
  rootId: string;
  /** Default 2 per ADR-028's MVP lens depth. */
  depth?: number;
  /** Fires when a relation is clicked in the canvas. */
  onSelectRelation?: (edge: ApiKnowledgeNeighborhoodEdge) => void;
}

/** Project the focused-neighborhood edge onto the shared `ApiGraphEdge`
 *  shape so the existing NVL adapter can render it without a per-shape
 *  branch. The scoring fields live in `properties` already. */
function edgeForNvl(edge: ApiKnowledgeNeighborhoodEdge): ApiGraphEdge {
  return {
    id: edge.id,
    kind: edge.kind,
    source_id: edge.source_id,
    target_id: edge.target_id,
    properties: edge.properties,
  };
}

export function FocusedLens({
  rootKind,
  rootId,
  depth = 2,
  onSelectRelation,
}: FocusedLensProps) {
  const [neighborhood, setNeighborhood] =
    useState<ApiKnowledgeNeighborhood | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNeighborhood(null);
    getKnowledgeNeighborhood({ rootKind, rootId, depth })
      .then((response) => {
        if (cancelled) return;
        setNeighborhood(response);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) setError(err);
        else if (err instanceof Error) setError(err.message);
        else setError("Failed to load neighborhood.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [rootKind, rootId, depth]);

  if (loading) {
    return (
      <p className="muted" role="status" aria-live="polite">
        Loading focused lens…
      </p>
    );
  }
  if (error !== null) {
    return (
      <div className="notice danger" role="alert">
        <strong>Failed to load lens</strong>
        <span>{error instanceof ApiError ? error.detail : error}</span>
      </div>
    );
  }
  if (neighborhood === null) return null;

  const nvlNodes = toNvlNodes(neighborhood.nodes);
  const nvlRelationships = toNvlRelationships(
    neighborhood.edges.map(edgeForNvl),
  );

  // Edge-id lookup so the click handler can resurface the original
  // scored edge instead of the projected GraphEdge.
  const edgesById = new Map(neighborhood.edges.map((e) => [e.id, e]));

  return (
    <div className="explore-lens" data-testid="focused-lens">
      <div className="explore-lens-meta">
        <span className="muted">
          Root {neighborhood.root_kind} · depth {neighborhood.depth} ·{" "}
          {neighborhood.nodes.length} nodes · {neighborhood.edges.length}{" "}
          edges
        </span>
        {neighborhood.truncated ? (
          <span
            className="muted"
            data-testid="focused-lens-truncated"
          >
            +{neighborhood.hidden_edge_count} more edges,{" "}
            {neighborhood.hidden_node_count} hidden nodes
          </span>
        ) : null}
      </div>
      <div className="explore-lens-canvas" data-testid="focused-lens-canvas">
        <InteractiveNvlWrapper
          nvlOptions={{ initialZoom: 0.8 }}
          nodes={nvlNodes}
          rels={nvlRelationships}
          mouseEventCallbacks={{
            onRelationshipClick: onSelectRelation
              ? (rel: Relationship) => {
                  const edge = edgesById.get(rel.id);
                  if (edge) onSelectRelation(edge);
                }
              : undefined,
          }}
        />
      </div>
    </div>
  );
}

export default FocusedLens;
