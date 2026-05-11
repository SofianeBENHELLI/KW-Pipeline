import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
} from "react";

/* ---------- Button ---------- */

type BtnKind = "default" | "primary" | "ghost" | "danger";
type BtnSize = "sm" | "xs";

export interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: BtnKind;
  size?: BtnSize;
  icon?: ReactNode;
  iconOnly?: boolean;
}

export function Btn({
  kind = "default",
  size = "sm",
  icon,
  iconOnly = false,
  className,
  children,
  type = "button",
  ...rest
}: BtnProps) {
  const classes = ["orb-btn"];
  if (kind === "primary") classes.push("orb-btn--primary");
  if (kind === "ghost") classes.push("orb-btn--ghost");
  if (kind === "danger") classes.push("orb-btn--danger");
  if (size === "xs") classes.push("orb-btn--xs");
  if (iconOnly) classes.push("orb-btn--icon");
  if (className) classes.push(className);
  return (
    <button type={type} className={classes.join(" ")} {...rest}>
      {icon}
      {!iconOnly && children}
    </button>
  );
}

/* ---------- Input ---------- */

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export function Input({ className, ...rest }: InputProps) {
  return <input className={["orb-input", className].filter(Boolean).join(" ")} {...rest} />;
}

/* ---------- Kbd ---------- */

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={["orb-kbd", className].filter(Boolean).join(" ")}>{children}</span>;
}

/* ---------- Chip ---------- */

export interface ChipProps extends HTMLAttributes<HTMLSpanElement> {
  color?: string;
  dot?: boolean;
}

export function Chip({ color, dot = false, className, children, style, ...rest }: ChipProps) {
  const mergedStyle = color ? { ...style, color } : style;
  return (
    <span className={["orb-chip", className].filter(Boolean).join(" ")} style={mergedStyle} {...rest}>
      {dot && <span className="dot" />}
      <span style={{ color: "var(--orb-fg-muted)" }}>{children}</span>
    </span>
  );
}

/* ---------- Card ---------- */

export type CardProps = HTMLAttributes<HTMLDivElement>;

export function Card({ className, children, ...rest }: CardProps) {
  return (
    <div className={["orb-card", className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </div>
  );
}

/* ---------- Rule ---------- */

export function Rule({ vertical = false }: { vertical?: boolean }) {
  return <div className={vertical ? "orb-vrule" : "orb-rule"} role="separator" aria-orientation={vertical ? "vertical" : "horizontal"} />;
}

/* ---------- SectionHeading ---------- */

export function SectionHeading({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={["orb-section-h", className].filter(Boolean).join(" ")}>{children}</div>;
}

/* ---------- MetaRow ---------- */

export interface MetaRowProps {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}

export function MetaRow({ label, children, className }: MetaRowProps) {
  return (
    <div className={["orb-meta-row", className].filter(Boolean).join(" ")}>
      <span className="k">{label}</span>
      <span className="v">{children}</span>
    </div>
  );
}

/* ---------- Mono ---------- */

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={["orb-mono", className].filter(Boolean).join(" ")}>{children}</span>;
}
