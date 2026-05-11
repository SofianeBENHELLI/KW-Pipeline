import { Icon, Kbd, SectionHeading } from "../ui/orb";
import { Input } from "../ui/orb/atoms";

export type CatalogView = "recent" | "review" | "validated" | "failed";

interface ViewDef {
  id: CatalogView;
  label: string;
  hint: string;
}

const VIEWS: ViewDef[] = [
  { id: "recent", label: "Recent", hint: "all" },
  { id: "review", label: "Review", hint: "NEEDS_REVIEW" },
  { id: "validated", label: "Validated", hint: "VALIDATED" },
  { id: "failed", label: "Failed", hint: "FAILED" },
];

export interface CatalogRailProps {
  view: CatalogView;
  onView: (next: CatalogView) => void;
  query: string;
  onQuery: (next: string) => void;
  counts?: Partial<Record<CatalogView, number>>;
}

/**
 * Left rail of the catalog screen — Phase 1 of the redesign. Renders the
 * filename filter + saved-view list. Counts are optional; the rail stays
 * functional without them (initial render before the first fetch
 * completes).
 */
export function CatalogRail({ view, onView, query, onQuery, counts }: CatalogRailProps) {
  return (
    <div className="orb-rail">
      <div className="orb-rail__head">
        <label className="orb-rail__search" aria-label="Filter documents by filename">
          <span className="orb-rail__search-icon">
            <Icon name="search" aria-hidden />
          </span>
          <Input
            type="search"
            placeholder="Filter filename…"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
          />
          <span className="orb-rail__search-kbd">
            <Kbd>/</Kbd>
          </span>
        </label>
      </div>
      <div className="orb-rail__group-label">
        <SectionHeading>Saved views</SectionHeading>
      </div>
      <nav className="orb-rail__views" aria-label="Saved views">
        {VIEWS.map((definition) => {
          const active = definition.id === view;
          const count = counts?.[definition.id];
          return (
            <button
              key={definition.id}
              type="button"
              className={`orb-rail__view ${active ? "is-active" : ""}`.trim()}
              aria-current={active ? "page" : undefined}
              onClick={() => onView(definition.id)}
            >
              <span>{definition.label}</span>
              <span className="orb-rail__view-count">{count?.toLocaleString() ?? ""}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

/** Resolve the active view to a list of `DocumentVersionStatus` strings. */
export function viewToStatuses(view: CatalogView): string[] {
  switch (view) {
    case "review":
      return ["NEEDS_REVIEW"];
    case "validated":
      return ["VALIDATED"];
    case "failed":
      return ["FAILED"];
    case "recent":
    default:
      return [];
  }
}
