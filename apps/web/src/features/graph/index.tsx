/**
 * Public surface of the knowledge-graph feature.
 *
 * Consumers (App, ReviewWorkspace, …) import from here so the
 * `@neo4j-nvl/react` dep stays contained inside this module.
 *
 * The view is wrapped in `React.lazy` + `<Suspense>` so the
 * `@neo4j-nvl/base` runtime (~2 MB / 600 KB gz) is split into its own
 * chunk and only fetched when the graph panel actually mounts. The
 * exported component preserves the original prop surface so callers
 * don't need to change.
 */
import { Suspense, lazy } from "react";
import type { ComponentProps } from "react";

const LazyKnowledgeGraphView = lazy(() => import("./KnowledgeGraphView"));

type KnowledgeGraphViewProps = ComponentProps<typeof LazyKnowledgeGraphView>;

export function KnowledgeGraphView(props: KnowledgeGraphViewProps) {
  return (
    <Suspense
      fallback={
        <p className="muted" role="status">
          Loading…
        </p>
      }
    >
      <LazyKnowledgeGraphView {...props} />
    </Suspense>
  );
}
