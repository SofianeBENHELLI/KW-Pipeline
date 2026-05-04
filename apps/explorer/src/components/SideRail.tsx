/**
 * 44 px side-rail navigation for the Knowledge Explorer.
 *
 * Three modes only — Browse / Document / Graph — because the Explorer
 * is read-only. The rail mirrors apps/widget's interaction contract
 * (ArrowUp/ArrowDown to move focus, Enter/Space to activate,
 * `aria-current="page"` on the active tab) so both widgets feel the
 * same to keyboard and screen-reader users.
 */

import React, { useCallback } from "react";

import { Icon, type IconName } from "./icons";

export type ActiveMode = "browse" | "document" | "graph";

interface RailItem {
  id: ActiveMode;
  icon: IconName;
  label: string;
}

const ITEMS: RailItem[] = [
  { id: "browse", icon: "files", label: "Browse documents" },
  { id: "document", icon: "docs", label: "Document viewer" },
  { id: "graph", icon: "graph", label: "Knowledge graph" },
];

interface Props {
  active: ActiveMode;
  onChange: (next: ActiveMode) => void;
  /** Whether the backend health probe last reported `ok`. */
  healthOk: boolean;
  /** Disable the Document tab when nothing is selected yet. */
  documentDisabled?: boolean;
}

export const SideRail: React.FC<Props> = ({
  active,
  onChange,
  healthOk,
  documentDisabled = false,
}) => {
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      const idx = ITEMS.findIndex((it) => it.id === active);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const next = ITEMS[(idx + delta + ITEMS.length) % ITEMS.length];
      // Skip the disabled Document tab on keyboard nav.
      if (next.id === "document" && documentDisabled) {
        const wrap = ITEMS[(idx + 2 * delta + ITEMS.length) % ITEMS.length];
        onChange(wrap.id);
        return;
      }
      onChange(next.id);
    },
    [active, onChange, documentDisabled],
  );

  return (
    <nav className="kw-rail" aria-label="Explorer mode" onKeyDown={onKeyDown}>
      {ITEMS.map((it) => {
        const isActive = it.id === active;
        const isDisabled = it.id === "document" && documentDisabled;
        return (
          <button
            key={it.id}
            type="button"
            className={isActive ? "kw-rail__btn kw-rail__btn--active" : "kw-rail__btn"}
            aria-label={it.label}
            aria-current={isActive ? "page" : undefined}
            title={isDisabled ? `${it.label} — pick a document first` : it.label}
            disabled={isDisabled}
            onClick={() => onChange(it.id)}
          >
            <Icon name={it.icon} size={16} />
          </button>
        );
      })}
      <span className="kw-rail__spacer" />
      <span className="kw-rail__divider" aria-hidden="true" />
      <span
        className={
          healthOk ? "kw-rail__status kw-rail__status--ok" : "kw-rail__status kw-rail__status--err"
        }
        title={healthOk ? "Backend ok" : "Backend unreachable"}
        aria-label={healthOk ? "Backend ok" : "Backend unreachable"}
      />
    </nav>
  );
};
