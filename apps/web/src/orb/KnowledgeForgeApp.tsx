/**
 * KnowledgeForgeApp — entry point for the new `/kf/*` route family.
 *
 * Internal codename: Orbital. User-visible product: **Knowledge Forge**.
 *
 * PR roadmap (all 8 PRs landed by PR 8):
 *   PR 1 — chrome only (DxShell + atoms + /kf placeholder).
 *   PR 2 — Review Workspace skeleton at `/kf/review[/:docId]`.
 *   PR 3 — Linked View tab inside Review Workspace.
 *   PR 4 — Review + Pipeline tabs + batch operations.
 *   PR 5 — `/kf/catalog`.
 *   PR 6 — `/kf/graph`.
 *   PR 7 — `/kf/search`, `/kf/chat`.
 *   PR 8 — `/kf/admin/*`, `/kf/settings`, deletes the `features-orb/`
 *           preview tree + its `/orb*` routes (now redundant with the
 *           full Knowledge Forge surface).
 */
import { lazy, Suspense, useState } from "react";
import type { ReactElement } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { CatalogView } from "./catalog/CatalogView";
import { GraphView } from "./graph/GraphView";
import { ReviewWorkspace } from "./review/ReviewWorkspace";
import { ChatPanel } from "./search/ChatPanel";
import { SearchPanel } from "./search/SearchPanel";
import { AdminHub } from "./admin/AdminHub";
import { DxShell } from "./shell/DxShell";

// SettingsModal pulls in MetaRow + admin chrome — lazy so the cold
// chunk only loads when the user opens it via the cog button or
// /kf/settings.
const SettingsModal = lazy(() =>
  import("./admin/SettingsModal").then((m) => ({ default: m.SettingsModal })),
);

import "./tokens.css";

export interface KnowledgeForgeAppProps {
  /** Optional override for the brand crumb (e.g. pipeline name). */
  pipelineName?: string;
}

export function KnowledgeForgeApp({
  pipelineName,
}: KnowledgeForgeAppProps = {}): ReactElement {
  const crumb = pipelineName ? `${pipelineName} · alpha` : "alpha";
  const [settingsOpen, setSettingsOpen] = useState(false);
  const location = useLocation();
  // /kf/settings → open the modal AND render the workspace below it
  // so closing the modal returns the user to a real surface, not a
  // blank page.
  const settingsRoute = location.pathname.startsWith("/kf/settings");

  // Pick the active top-tab from the URL so the chrome highlights the
  // right item without each route having to forward the prop.
  const activeTab = pickActiveTab(location.pathname);

  return (
    <DxShell
      activeTab={activeTab}
      activeRail={pickActiveRail(activeTab)}
      topBar={{
        product: "Knowledge Forge",
        crumb,
        status: "alpha · ok",
        initials: "KF",
        onOpenSettings: () => setSettingsOpen(true),
      }}
    >
      <Routes>
        <Route index element={<Navigate to="/kf/review" replace />} />
        <Route path="review" element={<ReviewWorkspace />} />
        <Route path="review/:docId" element={<ReviewWorkspace />} />
        <Route path="catalog/*" element={<CatalogView />} />
        <Route path="graph/*" element={<GraphView />} />
        <Route path="search/*" element={<SearchPanel />} />
        <Route path="chat/*" element={<ChatPanel />} />
        <Route path="admin/*" element={<AdminHub />} />
        <Route
          path="settings/*"
          element={<Navigate to="/kf/review" replace />}
        />
        <Route path="*" element={<Navigate to="/kf/review" replace />} />
      </Routes>
      <Suspense fallback={null}>
        {(settingsOpen || settingsRoute) && (
          <SettingsModal
            open
            onClose={() => setSettingsOpen(false)}
          />
        )}
      </Suspense>
    </DxShell>
  );
}

function pickActiveTab(
  pathname: string,
): "review" | "graph" | "search" | "chat" | "admin" | undefined {
  if (pathname.startsWith("/kf/review") || pathname === "/kf") return "review";
  if (pathname.startsWith("/kf/graph")) return "graph";
  if (pathname.startsWith("/kf/search")) return "search";
  if (pathname.startsWith("/kf/chat")) return "chat";
  if (pathname.startsWith("/kf/admin")) return "admin";
  return undefined;
}

/**
 * Map the top-bar nav tab onto the icon-rail tile. Chat + Admin
 * collapse onto the rail's "settings" / "review" anchors since the
 * rail's vocabulary differs slightly from the top-bar nav.
 */
function pickActiveRail(
  tab: ReturnType<typeof pickActiveTab>,
): "review" | "graph" | "search" | "settings" {
  if (tab === "graph") return "graph";
  if (tab === "search") return "search";
  if (tab === "admin") return "settings";
  return "review";
}

export default KnowledgeForgeApp;
