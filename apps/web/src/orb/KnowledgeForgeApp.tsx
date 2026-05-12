/**
 * KnowledgeForgeApp — entry point for the new `/kf/*` route family.
 *
 * Internal codename: Orbital. User-visible product: **Knowledge Forge**.
 *
 * PR 1 ships the chrome only — top bar, icon rail, and a placeholder
 * panel announcing the redesign. Subsequent PRs replace `<Placeholder>`
 * with the real workspace tree:
 *   PR 2 — `/kf/review`, `/kf/review/:docId`
 *   PR 3 — Linked View tab inside `/kf/review/:docId`
 *   PR 4 — Review + Pipeline tabs + batch operations
 *   PR 5 — `/kf/catalog`
 *   PR 6 — `/kf/graph`
 *   PR 7 — `/kf/search`, `/kf/chat`
 *   PR 8 — `/kf/admin/*`, `/kf/settings`
 */
import type { ReactElement } from "react";
import { Route, Routes } from "react-router-dom";

import { Kbd } from "./atoms/Kbd";
import { DxShell } from "./shell/DxShell";

import "./tokens.css";

function Placeholder(): ReactElement {
  return (
    <div className="dx-placeholder">
      <h2>Knowledge Forge — coming online</h2>
      <p>
        The reviewer workspace, knowledge graph, search, chat, and admin
        surfaces ship in subsequent PRs. The shell, design tokens, and
        atom library are wired and ready.
      </p>
      <div className="orb-kbd-row">
        <Kbd>R</Kbd>
        <span>Review</span>
        <Kbd>G</Kbd>
        <span>Graph</span>
        <Kbd>S</Kbd>
        <span>Search</span>
      </div>
    </div>
  );
}

export interface KnowledgeForgeAppProps {
  /** Optional override for the brand crumb (e.g. pipeline name). */
  pipelineName?: string;
}

export function KnowledgeForgeApp({
  pipelineName,
}: KnowledgeForgeAppProps = {}): ReactElement {
  const crumb = pipelineName ? `${pipelineName} · alpha` : "alpha";
  return (
    <DxShell
      activeTab="review"
      activeRail="review"
      topBar={{
        product: "Knowledge Forge",
        crumb,
        status: "alpha · ok",
        initials: "KF",
      }}
    >
      <Routes>
        <Route index element={<Placeholder />} />
        {/* PRs 2-8 register additional `/kf/*` routes here. */}
        <Route path="*" element={<Placeholder />} />
      </Routes>
    </DxShell>
  );
}

export default KnowledgeForgeApp;
