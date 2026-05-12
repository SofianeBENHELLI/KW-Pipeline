/**
 * DocTabs — Linked / Pipeline strip beneath the document header.
 * Per design §3.3 (the prototype canvas):
 *
 *   [Linked view*]  [Pipeline & FSM]    contextual hint →
 *
 * The asterisk denotes the default-on tab (Linked View). The right-side
 * hint switches text per active tab so the surface always tells the
 * reviewer "what does hovering / clicking do here".
 *
 * Note on naming: the URL still accepts `?tab=review` as a legacy
 * alias (mapped to `pipeline`) so any links saved between the
 * three-tab interim and this two-tab cutover keep working.
 */

import type { ReactElement } from "react";

import { OrbI } from "../index";

export type DocTab = "linked" | "pipeline";

interface TabDef {
  id: DocTab;
  label: string;
  icon: ReactElement;
  hint: string;
}

const TABS: TabDef[] = [
  {
    id: "linked",
    label: "Linked view",
    icon: OrbI.graph,
    hint: "hover any object — its source span(s) highlight in the document, and vice-versa",
  },
  {
    id: "pipeline",
    label: "Pipeline & FSM",
    icon: OrbI.bolt,
    hint: "lifecycle · extraction · semantic · versions",
  },
];

export interface DocTabsProps {
  active: DocTab;
  onChange: (tab: DocTab) => void;
}

export function DocTabs({ active, onChange }: DocTabsProps): ReactElement {
  const hint = TABS.find((t) => t.id === active)?.hint ?? "";
  return (
    <div className="kf-doctabs" role="tablist" aria-label="Document workspace tabs">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={active === t.id}
          aria-current={active === t.id ? "page" : undefined}
          className={`kf-doctab ${active === t.id ? "is-active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          <span aria-hidden="true">{t.icon}</span>
          {t.label}
          {t.id === "linked" && (
            <span className="kf-doctab__tag orb-mono">default</span>
          )}
        </button>
      ))}
      <span className="kf-doctabs__hint orb-mono" aria-live="polite">
        {hint}
      </span>
    </div>
  );
}
