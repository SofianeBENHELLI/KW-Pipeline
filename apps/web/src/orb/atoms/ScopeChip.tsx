/**
 * ScopeChip — small colored-dot + muted-label chip for a document scope.
 *
 * Three known scopes (`personal`, `community`, `project`) each map to a
 * Knowledge Forge semantic hue. Unknown scopes render as "personal"
 * (info hue) so the chip keeps its shape rather than disappearing.
 */
import type { CSSProperties, ReactElement } from "react";

export type DocScope = "personal" | "community" | "project";

interface ScopeEntry {
  label: string;
  /** A CSS variable reference for the dot color. */
  color: string;
}

const SCOPES: Record<DocScope, ScopeEntry> = {
  personal:  { label: "personal",  color: "var(--orb-info)" },
  community: { label: "community", color: "var(--orb-purple)" },
  project:   { label: "project",   color: "var(--orb-ok)" },
};

export interface ScopeChipProps {
  scope: DocScope | string;
}

export function ScopeChip({ scope }: ScopeChipProps): ReactElement {
  const s = SCOPES[scope as DocScope] ?? SCOPES.personal;
  const dotStyle: CSSProperties = { color: s.color };
  return (
    <span className="orb-chip" style={dotStyle}>
      <span className="dot" aria-hidden="true" />
      <span style={{ color: "var(--orb-fg-muted)" }}>{s.label}</span>
    </span>
  );
}
