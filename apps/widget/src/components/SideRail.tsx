/**
 * 44 px side-rail navigation. Replaces the previous "all four cards
 * stacked" body layout with a single-active-mode layout. One icon
 * button per mode, optional badge dot for in-flight counts, and a
 * footer status indicator that mirrors backend reachability so the
 * user can glance at the rail and know the API is healthy without
 * switching to the Health mode.
 *
 * Keyboard: ArrowUp / ArrowDown move focus between rail buttons;
 * Enter / Space activates. `aria-current="page"` on the active
 * button so screen readers announce the change.
 */

import React, { useCallback } from "react";

import { Icon, type IconName } from "./icons";

export type ActiveMode = "health" | "upload" | "docs" | "kg" | "search" | "chat";

interface RailItem {
  id: ActiveMode;
  icon: IconName;
  label: string;
  /** Optional small numeric badge (e.g. files in-flight). */
  badge?: number;
}

const ITEMS: RailItem[] = [
  { id: "health", icon: "pulse", label: "Backend health" },
  { id: "upload", icon: "upload-cloud", label: "Upload" },
  { id: "docs", icon: "docs", label: "Recent documents" },
  { id: "search", icon: "search", label: "Knowledge search" },
  { id: "chat", icon: "info", label: "Knowledge chat" },
  { id: "kg", icon: "graph", label: "Knowledge layer" },
];

interface Props {
  active: ActiveMode;
  onChange: (next: ActiveMode) => void;
  /** In-flight upload count; rendered as a pill badge on the upload icon. */
  uploadInFlight?: number;
  /** Whether the backend health probe last reported `ok`. */
  healthOk: boolean;
}

export const SideRail: React.FC<Props> = ({
  active,
  onChange,
  uploadInFlight = 0,
  healthOk,
}) => {
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      const idx = ITEMS.findIndex((it) => it.id === active);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const next = ITEMS[(idx + delta + ITEMS.length) % ITEMS.length];
      onChange(next.id);
    },
    [active, onChange],
  );

  return (
    <nav
      className="kw-rail"
      aria-label="Widget mode"
      onKeyDown={onKeyDown}
    >
      {ITEMS.map((it) => {
        const isActive = it.id === active;
        const badge =
          it.id === "upload" && uploadInFlight > 0 ? uploadInFlight : null;
        return (
          <button
            key={it.id}
            type="button"
            className={isActive ? "kw-rail__btn kw-rail__btn--active" : "kw-rail__btn"}
            aria-label={it.label}
            aria-current={isActive ? "page" : undefined}
            title={it.label}
            onClick={() => onChange(it.id)}
          >
            <Icon name={it.icon} size={16} />
            {badge !== null && (
              <span className="kw-rail__badge" aria-hidden="true">
                {badge}
              </span>
            )}
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
