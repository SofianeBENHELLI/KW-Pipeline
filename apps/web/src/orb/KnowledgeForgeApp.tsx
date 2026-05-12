/**
 * KnowledgeForgeApp — entry point for the new `/kf/*` route family.
 *
 * Internal codename: Orbital. User-visible product: **Knowledge Forge**.
 *
 * PR roadmap:
 *   PR 1 — chrome only (DxShell + atoms + /kf placeholder).
 *   PR 2 — Review Workspace skeleton at `/kf/review[/:docId]` (this PR).
 *   PR 3 — Linked View tab inside Review Workspace.
 *   PR 4 — Review + Pipeline tabs + batch operations.
 *   PR 5 — `/kf/catalog`.
 *   PR 6 — `/kf/graph`.
 *   PR 7 — `/kf/search`, `/kf/chat`.
 *   PR 8 — `/kf/admin/*`, `/kf/settings`. Flips `/` redirect, narrows
 *           the legacy catch-all, and deletes `features-orb/`.
 */
import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Kbd } from "./atoms/Kbd";
import { CatalogView } from "./catalog/CatalogView";
import { GraphView } from "./graph/GraphView";
import { ReviewWorkspace } from "./review/ReviewWorkspace";
import { ChatPanel } from "./search/ChatPanel";
import { SearchPanel } from "./search/SearchPanel";
import { DxShell } from "./shell/DxShell";

import "./tokens.css";

function ComingSoon({ title }: { title: string }): ReactElement {
  return (
    <div className="dx-placeholder" data-testid={`kf-coming-soon-${title.toLowerCase()}`}>
      <h2>{title} — coming soon</h2>
      <p>
        This surface ships in a later PR of the Knowledge Forge redesign.
        For now the chrome is wired so deep links don&apos;t 404.
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
        {/* `/kf` opens straight on the Review Workspace once PR 2 lands. */}
        <Route index element={<Navigate to="/kf/review" replace />} />
        <Route path="review" element={<ReviewWorkspace />} />
        <Route path="review/:docId" element={<ReviewWorkspace />} />
        {/* Stubs for PRs 5-8 so the top-bar nav doesn't 404. */}
        <Route path="catalog/*" element={<CatalogView />} />
        <Route path="graph/*" element={<GraphView />} />
        <Route path="search/*" element={<SearchPanel />} />
        <Route path="chat/*" element={<ChatPanel />} />
        <Route path="admin/*" element={<ComingSoon title="Admin" />} />
        <Route path="settings/*" element={<ComingSoon title="Settings" />} />
        <Route path="*" element={<ComingSoon title="Knowledge Forge" />} />
      </Routes>
    </DxShell>
  );
}

export default KnowledgeForgeApp;
