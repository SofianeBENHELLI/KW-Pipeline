/**
 * Scope chip — surfaces the workspace scopes a document was uploaded
 * into (EPIC-D #218 / #250).
 *
 * The backend persists one row per ``(kind, ref)`` link in the
 * ``document_scopes`` join table; the upload route returns the full
 * list as ``UploadDocumentResponse.scopes``. This component renders
 * the first scope as a compact icon+label chip, with an explicit
 * "+N more" badge when the document is linked into multiple scopes
 * (the badge's tooltip lists the rest so reviewers don't have to
 * dig into the audit trail to know where the doc landed).
 *
 * Pre-#250 the read-side ``Document`` shape did NOT carry ``scopes``
 * (only the upload-time response did), so this component accepts an
 * optional ``scopes`` prop and renders a "No scope info" placeholder
 * when the field is missing or empty. When ``GET /documents`` is
 * extended to surface scopes (tracked alongside D.5), the placeholder
 * branch is dead code and can be deleted.
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
  // Pre-#250 ``Document`` doesn't carry scopes on the catalog read
  // path (#250 only added the field on ``UploadDocumentResponse``).
  // TODO(EPIC-D D.5): drop this branch once ``GET /documents`` exposes
  // ``scopes`` on every document — until then, surface a neutral
  // placeholder so reviewers know the chip slot is intentional rather
  // than a render bug.
  if (!scopes || scopes.length === 0) {
    return (
      <span
        className="scope-chip scope-chip--empty"
        data-testid="scope-chip-empty"
        title="Scope info is not available on this view yet (EPIC-D D.5)."
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
