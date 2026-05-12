/**
 * Btn — Knowledge Forge action button.
 *
 * Variants: `default | primary | ghost | danger` × optional `xs` size and
 * `icon` (square 28px, no label) modifier. Inherits all native
 * `<button>` props so callers can add `onClick`, `disabled`, `title`,
 * `aria-*`, etc. without any extra boilerplate.
 */
import type { ButtonHTMLAttributes, ReactElement, ReactNode } from "react";

export type BtnKind = "default" | "primary" | "ghost" | "danger";

export interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: BtnKind;
  /** Leading icon. Pass `<OrbI.search />` etc. */
  icon?: ReactNode;
  /** Compact 22px height for inline contexts. */
  xs?: boolean;
  /** Square 28px button with no text — pass icon only. */
  iconOnly?: boolean;
}

const KIND_CLASS: Record<BtnKind, string> = {
  default: "",
  primary: "orb-btn--primary",
  ghost:   "orb-btn--ghost",
  danger:  "orb-btn--danger",
};

export function Btn({
  kind = "default",
  icon,
  xs,
  iconOnly,
  children,
  className,
  type,
  ...rest
}: BtnProps): ReactElement {
  const classes = [
    "orb-btn",
    KIND_CLASS[kind],
    xs && "orb-btn--xs",
    iconOnly && "orb-btn--icon",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type ?? "button"} className={classes} {...rest}>
      {icon && (
        <span style={{ display: "inline-flex" }} aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
    </button>
  );
}
