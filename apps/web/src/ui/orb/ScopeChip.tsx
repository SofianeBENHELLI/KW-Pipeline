import type { components } from "../../api/generated/schema";

type Scope = components["schemas"]["Scope"];
type ScopeKind = Scope["kind"];

const SCOPE_COLORS: Record<ScopeKind, string> = {
  personal: "var(--orb-info)",
  swym_community: "var(--orb-purple)",
  project: "var(--orb-ok)",
};

const SCOPE_LABELS: Record<ScopeKind, string> = {
  personal: "personal",
  swym_community: "community",
  project: "project",
};

export interface OrbScopeChipProps {
  scope: ScopeKind;
  className?: string;
  title?: string;
}

/**
 * Renders a single scope chip with a colored dot. For documents with
 * multiple scopes, render one per scope or compose your own "+N more"
 * affordance — keeping this atom single-purpose mirrors the mockup.
 */
export function OrbScopeChip({ scope, className, title }: OrbScopeChipProps) {
  const color = SCOPE_COLORS[scope] ?? "var(--orb-fg-muted)";
  const label = SCOPE_LABELS[scope] ?? scope;
  return (
    <span
      className={["orb-chip", className].filter(Boolean).join(" ")}
      style={{ color }}
      title={title ?? label}
      aria-label={`scope: ${label}`}
    >
      <span className="dot" aria-hidden="true" />
      <span style={{ color: "var(--orb-fg-muted)" }}>{label}</span>
    </span>
  );
}
