/**
 * Scope chip — surfaces the workspace scopes a document was uploaded
 * into (EPIC-D #218 / #250 / #258).
 *
 * The backend persists one row per ``(kind, ref)`` link in the
 * ``document_scopes`` join table; the upload route and every catalog
 * read endpoint return the full list as ``Document.scopes``
 * (#258 wired the read side). This component renders the first scope
 * as a compact icon+label chip, with an explicit "+N more" badge
 * when the document is linked into multiple scopes (the badge's
 * tooltip lists the rest so reviewers don't have to dig into the
 * audit trail to know where the doc landed).
 *
 * The ``scopes`` prop accepts ``null`` / ``undefined`` for resilience
 * against pre-#258 cached schemas, but in steady state the empty-array
 * branch is the only fallback callers hit when a document has no
 * active scope link (e.g. all links soft-removed per the no-delete
 * policy). Either way the chip surfaces a neutral "No scope info"
 * placeholder so the row layout is stable.
 *
 * Visual layout choice: icon + short label, with the ref folded into
 * the title attribute rather than the chip body. This keeps the chip
 * the same width regardless of how long an opaque community id or
 * project key is, so the surrounding row layout stays stable.
 */

import type { components } from "../api/generated/schema";

type Scope = components["schemas"]["Scope"];
type ScopeKind = Scope["kind"];

interface ScopeChipProps {
  /** Scopes attached to the document, in catalog insertion order. */
  scopes: ReadonlyArray<Scope> | null | undefined;
}

interface KindMeta {
  label: string;
  icon: string;
  description: string;
}

// Glyphs are lucide-style emoji stand-ins so the chip stays
// dependency-free; an SVG icon system can swap them in later without
// touching the component contract.
const KIND_META: Record<ScopeKind, KindMeta> = {
  personal: {
    label: "Personal",
    icon: "\u{1F464}", // bust-in-silhouette
    description: "Visible only to the uploader.",
  },
  swym_community: {
    label: "Community",
    icon: "\u{1F310}", // globe
    description: "Linked to a 3DSwym community.",
  },
  project: {
    label: "Project",
    icon: "\u{1F5C2}\u{FE0F}", // card-index dividers
    description: "Linked to a project workspace.",
  },
};

function refTooltip(scope: Scope): string {
  const meta = KIND_META[scope.kind];
  return `${meta.label} — ${scope.ref}\n${meta.description}`;
}

export function ScopeChip({ scopes }: ScopeChipProps) {
  // Empty / missing scopes both render the neutral placeholder. With
  // #258 wired, ``scopes`` is always present on read responses; the
  // ``null`` / ``undefined`` guard stays for resilience against
  // pre-#258 cached schemas. An empty list means the document has no
  // active scope link (e.g. every link was soft-removed per #262).
  if (!scopes || scopes.length === 0) {
    return (
      <span
        className="scope-chip scope-chip--empty"
        data-testid="scope-chip-empty"
        title="No active scope links on this document."
        aria-label="No scope information available"
      >
        <span aria-hidden="true">—</span> No scope info
      </span>
    );
  }

  const [first, ...rest] = scopes;
  const meta = KIND_META[first.kind];
  const hasMore = rest.length > 0;
  const moreTooltip = rest.map((s) => refTooltip(s)).join("\n\n");

  return (
    <span className="scope-chip-group" data-testid="scope-chip-group">
      <span
        className={`scope-chip scope-chip--${first.kind}`}
        data-testid="scope-chip"
        data-scope-kind={first.kind}
        title={refTooltip(first)}
        aria-label={`${meta.label} scope: ${first.ref}`}
      >
        <span className="scope-chip__icon" aria-hidden="true">
          {meta.icon}
        </span>
        <span className="scope-chip__label">{meta.label}</span>
      </span>
      {hasMore ? (
        <span
          className="scope-chip scope-chip--more"
          data-testid="scope-chip-more"
          title={moreTooltip}
          aria-label={`+${rest.length} additional scope${
            rest.length === 1 ? "" : "s"
          }`}
        >
          +{rest.length} more
        </span>
      ) : null}
    </span>
  );
}
