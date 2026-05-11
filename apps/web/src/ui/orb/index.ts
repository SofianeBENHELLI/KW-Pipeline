/**
 * Orbital design-system barrel. Phase 0 of the redesign tracked in
 * `docs/roadmap/orbital-redesign.md`. Atoms live under `.orb-*` CSS
 * selectors defined in `src/styles/tokens.css` and are scoped to the
 * shell via `.orb-app` so they don't bleed into legacy `styles.css`.
 */

export { Btn, Card, Chip, Input, Kbd, MetaRow, Mono, Rule, SectionHeading } from "./atoms";
export type { BtnProps, CardProps, ChipProps, InputProps, MetaRowProps } from "./atoms";

export { OrbStatusBadge } from "./StatusBadge";
export type { OrbStatusBadgeProps } from "./StatusBadge";

export { OrbScopeChip } from "./ScopeChip";
export type { OrbScopeChipProps } from "./ScopeChip";

export { Icon } from "./Icon";
export type { IconName, IconProps } from "./Icon";

export { ThemeToggle } from "./ThemeToggle";

export { useOrbDensity, useOrbTheme } from "./useTheme";
export type { OrbDensity, OrbTheme } from "./useTheme";
