/**
 * Card — bordered surface (`bg-elev`, 8px radius).
 *
 * Most workspace panels are a Card. Compose freely:
 *   <Card><CardHead>…</CardHead><div className="…">…</div></Card>
 *
 * `CardHead` provides the standard "section header + right-aligned
 * actions" row used throughout the prototype.
 */
import type { HTMLAttributes, ReactElement, ReactNode } from "react";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

export function Card({ children, className, ...rest }: CardProps): ReactElement {
  const classes = ["orb-card", className].filter(Boolean).join(" ");
  return (
    <div className={classes} {...rest}>
      {children}
    </div>
  );
}

export interface CardHeadProps extends HTMLAttributes<HTMLDivElement> {
  /** Left side — typically a SectionH. */
  children: ReactNode;
  /** Right side — typically buttons or a small mono hint. */
  right?: ReactNode;
}

export function CardHead({
  children,
  right,
  className,
  ...rest
}: CardHeadProps): ReactElement {
  const classes = ["orb-card-head", className].filter(Boolean).join(" ");
  return (
    <div
      className={classes}
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "9px 14px",
        borderBottom: "1px solid var(--orb-rule)",
        gap: 10,
      }}
      {...rest}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>{children}</div>
      {right !== undefined && (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {right}
        </div>
      )}
    </div>
  );
}
