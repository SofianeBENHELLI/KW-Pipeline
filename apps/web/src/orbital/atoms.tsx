import type {
  ButtonHTMLAttributes,
  ReactNode,
  SVGProps,
} from "react";

/**
 * Atoms ported VERBATIM from the hi-fi mockup `orbital-shared.jsx`.
 * Class names + visual structure match the mockup 1:1 so layouts in
 * `styles.css` paint without any extra mapping.
 */

/* ───────────── Icons (22 inline SVGs, 16x16 viewBox, currentColor) ───────────── */

export type IconName =
  | "search" | "plus" | "check" | "x" | "chev" | "chevD" | "doc" | "filter"
  | "graph" | "spark" | "chat" | "cog" | "alert" | "trash" | "shield" | "bolt"
  | "archive" | "user" | "team" | "link" | "refresh" | "play" | "pause"
  | "dot" | "grip" | "ext";

const ICON_PATHS: Record<IconName, { viewBox: string; size: number; body: ReactNode; fill?: boolean }> = {
  search:  { viewBox: "0 0 16 16", size: 14, body: <><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5l3 3" /></> },
  plus:    { viewBox: "0 0 16 16", size: 14, body: <path d="M8 3v10M3 8h10" /> },
  check:   { viewBox: "0 0 16 16", size: 14, body: <path d="M3 8.5l3 3 7-7" /> },
  x:       { viewBox: "0 0 16 16", size: 14, body: <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" /> },
  chev:    { viewBox: "0 0 16 16", size: 12, body: <path d="M5 4l4 4-4 4" /> },
  chevD:   { viewBox: "0 0 16 16", size: 10, body: <path d="M4 6l4 4 4-4" /> },
  doc:     { viewBox: "0 0 16 16", size: 13, body: <><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3" /></> },
  filter:  { viewBox: "0 0 16 16", size: 13, body: <path d="M2 3h12M4 8h8M6 13h4" /> },
  graph:   { viewBox: "0 0 16 16", size: 13, body: <><circle cx="3.5" cy="3.5" r="1.5" /><circle cx="12.5" cy="3.5" r="1.5" /><circle cx="8" cy="12.5" r="1.5" /><path d="M4.6 4.6l2.4 6.8M11.4 4.6l-2.4 6.8M5 3.5h6" /></> },
  spark:   { viewBox: "0 0 16 16", size: 13, body: <path d="M8 2l1.5 4.5L14 8l-4.5 1.5L8 14l-1.5-4.5L2 8l4.5-1.5z" /> },
  chat:    { viewBox: "0 0 16 16", size: 13, body: <path d="M2 4a2 2 0 012-2h8a2 2 0 012 2v5a2 2 0 01-2 2H7l-3 3v-3H4a2 2 0 01-2-2z" /> },
  cog:     { viewBox: "0 0 16 16", size: 13, body: <><circle cx="8" cy="8" r="2" /><path d="M8 1v2M8 13v2M15 8h-2M3 8H1M12.95 3.05l-1.4 1.4M4.45 11.55l-1.4 1.4M12.95 12.95l-1.4-1.4M4.45 4.45l-1.4-1.4" /></> },
  alert:   { viewBox: "0 0 16 16", size: 13, body: <><path d="M8 2L1.5 13.5h13z" /><path d="M8 6v4M8 12v.5" /></> },
  trash:   { viewBox: "0 0 16 16", size: 13, body: <path d="M3 4h10M6 4V2.5h4V4M5 4l.5 9h5L11 4M7 7v4M9 7v4" /> },
  shield:  { viewBox: "0 0 16 16", size: 13, body: <path d="M8 1.5L2.5 4v4c0 3.5 2.5 6 5.5 6.5 3-.5 5.5-3 5.5-6.5V4z" /> },
  bolt:    { viewBox: "0 0 16 16", size: 13, body: <path d="M9 1L3 9h4l-1 6 6-8H8z" /> },
  archive: { viewBox: "0 0 16 16", size: 13, body: <><rect x="2" y="3" width="12" height="3" /><path d="M3 6v8h10V6M6.5 9h3" /></> },
  user:    { viewBox: "0 0 16 16", size: 13, body: <><circle cx="8" cy="5.5" r="2.5" /><path d="M3 14c0-2.5 2.2-4.5 5-4.5s5 2 5 4.5" /></> },
  team:    { viewBox: "0 0 16 16", size: 13, body: <><circle cx="6" cy="6" r="2" /><circle cx="11.5" cy="6.5" r="1.5" /><path d="M2 13c0-2 1.8-3.5 4-3.5s4 1.5 4 3.5M10.5 13c0-1.5 1-2.5 2.5-2.5s1.5 1 1.5 2.5" /></> },
  link:    { viewBox: "0 0 16 16", size: 13, body: <path d="M6.5 9.5L9.5 6.5M7 4l1-1.2a2.8 2.8 0 014 4l-1 1M9 12l-1 1.2a2.8 2.8 0 01-4-4l1-1" /> },
  refresh: { viewBox: "0 0 16 16", size: 13, body: <path d="M13 8a5 5 0 01-9.2 2.7M3 8a5 5 0 019.2-2.7M13 3v3h-3M3 13v-3h3" /> },
  play:    { viewBox: "0 0 16 16", size: 11, fill: true, body: <path d="M4 2l9 6-9 6z" /> },
  pause:   { viewBox: "0 0 16 16", size: 11, fill: true, body: <path d="M4 3h3v10H4zM9 3h3v10H9z" /> },
  dot:     { viewBox: "0 0 6 6",   size: 6,  fill: true, body: <circle cx="3" cy="3" r="3" /> },
  grip:    { viewBox: "0 0 10 10", size: 10, fill: true, body: <><circle cx="2.5" cy="2.5" r="1" /><circle cx="7.5" cy="2.5" r="1" /><circle cx="2.5" cy="5" r="1" /><circle cx="7.5" cy="5" r="1" /><circle cx="2.5" cy="7.5" r="1" /><circle cx="7.5" cy="7.5" r="1" /></> },
  ext:     { viewBox: "0 0 16 16", size: 10, body: <path d="M6 3H3v10h10V10M9 3h4v4M13 3L7 9" /> },
};

export interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

export function Icon({ name, size, "aria-label": ariaLabel, ...rest }: IconProps) {
  const def = ICON_PATHS[name];
  const dim = size ?? def.size;
  const props: SVGProps<SVGSVGElement> = def.fill
    ? { fill: "currentColor" }
    : { fill: "none", stroke: "currentColor", strokeWidth: 1.4 };
  return (
    <svg
      width={dim}
      height={dim}
      viewBox={def.viewBox}
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      role={ariaLabel ? "img" : undefined}
      {...props}
      {...rest}
    >
      {def.body}
    </svg>
  );
}

/* ───────────── StatusBadge ───────────── */

const STATUS_MOD: Record<string, { label: string; cls: string }> = {
  UPLOADED:              { label: "UPLOADED",         cls: "orb-status--stored" },
  HASHED:                { label: "HASHED",           cls: "orb-status--stored" },
  STORED:                { label: "STORED",           cls: "orb-status--stored" },
  QUEUED_FOR_EXTRACTION: { label: "QUEUED",           cls: "orb-status--stored" },
  EXTRACTING:            { label: "EXTRACTING",       cls: "orb-status--extracting" },
  EXTRACTED:             { label: "EXTRACTED",        cls: "orb-status--extracted" },
  SEMANTIC_READY:        { label: "SEMANTIC_READY",   cls: "orb-status--semantic" },
  NEEDS_REVIEW:          { label: "NEEDS_REVIEW",     cls: "orb-status--review" },
  VALIDATED:             { label: "VALIDATED",        cls: "orb-status--validated" },
  REJECTED:              { label: "REJECTED",         cls: "orb-status--rejected" },
  SUPERSEDED:            { label: "SUPERSEDED",       cls: "orb-status--duplicate" },
  FAILED:                { label: "FAILED",           cls: "orb-status--failed" },
  DUPLICATE_DETECTED:    { label: "DUPLICATE",        cls: "orb-status--duplicate" },
  PURGED:                { label: "PURGED",           cls: "orb-status--duplicate" },
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const s = (status && STATUS_MOD[status]) || { label: "UNKNOWN", cls: "orb-status--stored" };
  return (
    <span className={`orb-status ${s.cls}`}>
      <span className="dot"></span>
      {s.label}
    </span>
  );
}

/* ───────────── ScopeChip ───────────── */

const SCOPE_COLOR: Record<string, string> = {
  personal: "var(--orb-info)",
  swym_community: "var(--orb-purple)",
  project: "var(--orb-ok)",
};
const SCOPE_LABEL: Record<string, string> = {
  personal: "personal",
  swym_community: "community",
  project: "project",
};

export function ScopeChip({ scope }: { scope: string }) {
  const color = SCOPE_COLOR[scope] ?? "var(--orb-fg-muted)";
  const label = SCOPE_LABEL[scope] ?? scope;
  return (
    <span className="orb-chip" style={{ color }}>
      <span className="dot"></span>
      <span style={{ color: "var(--orb-fg-muted)" }}>{label}</span>
    </span>
  );
}

/* ───────────── Btn ───────────── */

type BtnKind = "default" | "primary" | "ghost" | "danger";

export interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: BtnKind;
  icon?: ReactNode;
  xs?: boolean;
}

export function Btn({ children, kind = "default", icon, xs, className, type = "button", ...rest }: BtnProps) {
  const classes = ["orb-btn"];
  if (kind === "primary") classes.push("orb-btn--primary");
  if (kind === "ghost") classes.push("orb-btn--ghost");
  if (kind === "danger") classes.push("orb-btn--danger");
  if (xs) classes.push("orb-btn--xs");
  if (className) classes.push(className);
  return (
    <button type={type} className={classes.join(" ")} {...rest}>
      {icon && <span style={{ display: "inline-flex" }}>{icon}</span>}
      {children}
    </button>
  );
}

/* ───────────── Kbd ───────────── */

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="orb-kbd">{children}</span>;
}

/* ───────────── MetaRow ───────────── */

export function MetaRow({ k, children }: { k: ReactNode; children: ReactNode }) {
  return (
    <div className="orb-meta-row">
      <span className="k">{k}</span>
      <span className="v">{children}</span>
    </div>
  );
}

/* ───────────── Inputs ───────────── */

import type { InputHTMLAttributes } from "react";

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export function Input({ className, ...rest }: InputProps) {
  return <input className={["orb-input", className].filter(Boolean).join(" ")} {...rest} />;
}
