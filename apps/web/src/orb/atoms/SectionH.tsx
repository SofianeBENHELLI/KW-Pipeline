/**
 * SectionH — uppercase mono section header (10px, letter-spaced).
 *
 * Used inside Card heads and rail subdivisions. Pure label, no semantic
 * heading level (consumers wrap in their own `<h2>`/`<h3>` if needed
 * for the document outline).
 */
import type { HTMLAttributes, ReactElement, ReactNode } from "react";

export interface SectionHProps extends HTMLAttributes<HTMLSpanElement> {
  children: ReactNode;
}

export function SectionH({
  children,
  className,
  ...rest
}: SectionHProps): ReactElement {
  const classes = ["orb-section-h", className].filter(Boolean).join(" ");
  return (
    <span className={classes} {...rest}>
      {children}
    </span>
  );
}
