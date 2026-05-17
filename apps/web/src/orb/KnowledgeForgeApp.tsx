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
 *   PR 6 — corpus `/kf/graph` (since retired — graph is a
 *           per-document tab on the Review Workspace; corpus-wide
 *           exploration belongs to the Knowledge Explorer app).
 *   PR 7 — `/kf/search`, `/kf/chat`.
 *   PR 8 — `/kf/admin/*`, `/kf/settings`, deletes the `features-orb/`
 *           preview tree + its `/orb*` routes (now redundant with the
 *           full Knowledge Forge surface).
 *
 * Post-cutover the icon-rail and top-bar nav are both wired to
 * react-router; clicking a tile or tab navigates without a
 * full-page reload.
 */
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { SessionExpiredBanner, useSessionGuard } from "../../../_shared/auth";
import {
  clearSessionTrigger,
  getApiBaseUrl,
  setSessionTrigger,
} from "../api/client";
import { useAdminConfig } from "../api/useAdminConfig";
import { ForceAutoBanner } from "./catalog/Banners";
import { CatalogView } from "./catalog/CatalogView";
import { ReviewWorkspace } from "./review/ReviewWorkspace";
import { ChatPanel } from "./search/ChatPanel";
import { SearchPanel } from "./search/SearchPanel";
import { AdminHub } from "./admin/AdminHub";
import { DxShell } from "./shell/DxShell";
import type { RailTileId } from "./shell/IconRail";
import type { TopNavTab } from "./shell/TopBar";

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
  const navigate = useNavigate();

  // ADR-019 §5 — register the orb shell with the session-expired
  // trigger so any 401 thrown from the api client flips the banner
  // here too. SessionGuardProvider sits at the top of main.tsx, so
  // we just read its value and wire the api hook.
  const session = useSessionGuard();
  useEffect(() => {
    setSessionTrigger(session.trigger);
    return () => clearSessionTrigger();
  }, [session.trigger]);
  const handleSignInAgain = useCallback(() => {
    if (typeof window !== "undefined") window.location.reload();
  }, []);

  // EPIC-A A.8 force-auto banner — surfaces ``force_auto_corpus=true``
  // for admins. Hidden for non-admin users (403) and on fetch error.
  const adminConfig = useAdminConfig(getApiBaseUrl());
  const forceAutoActive =
    adminConfig.status === "ok" &&
    adminConfig.config?.hitl?.force_auto_corpus === true;
  // /kf/settings → open the modal AND render the workspace below it
  // so closing the modal returns the user to a real surface, not a
  // blank page.
  const settingsRoute = location.pathname.startsWith("/kf/settings");

  // Pick the active top-tab from the URL so the chrome highlights the
  // right item without each route having to forward the prop.
  const activeTab = pickActiveTab(location.pathname);

  /**
   * Top-bar tab → route mapping. Mirrors the prototype's nav order
   * (Review · Graph · Search · Chat · Admin) per design §2.2.
   */
  const onTabSelect = (tab: TopNavTab) => {
    navigate(routeForTopTab(tab));
  };

  /**
   * Icon-rail tile → route mapping. The rail collapses Activity onto
   * the Admin hub (the prototype's "Activity" sparkline lives there)
   * and Upload + Document onto the Catalog surface (the catalog is
   * the bulk-upload table). Settings opens the modal directly so the
   * URL doesn't change for what's a transient overlay.
   */
  const onRailSelect = (tile: RailTileId) => {
    if (tile === "settings") {
      setSettingsOpen(true);
      return;
    }
    const target = routeForRailTile(tile);
    if (target) navigate(target);
  };

  return (
    <DxShell
      activeTab={activeTab}
      activeRail={pickActiveRail(location.pathname, activeTab)}
      onTabSelect={onTabSelect}
      onRailSelect={onRailSelect}
      topBar={{
        product: "Knowledge Forge",
        crumb,
        status: "alpha · ok",
        initials: "KF",
        onOpenSettings: () => setSettingsOpen(true),
      }}
    >
      <ForceAutoBanner hidden={!forceAutoActive} />
      <SessionExpiredBanner
        visible={session.expired}
        onSignIn={handleSignInAgain}
      />
      <Routes>
        <Route index element={<Navigate to="/kf/review" replace />} />
        <Route path="review" element={<ReviewWorkspace />} />
        <Route path="review/:docId" element={<ReviewWorkspace />} />
        <Route path="catalog/*" element={<CatalogView />} />
        {/* Legacy /kf/graph deep-links land on the Review Workspace —
            graph is now a per-document tab. Corpus-wide graph
            exploration belongs to the Knowledge Explorer app. */}
        <Route
          path="graph/*"
          element={<Navigate to="/kf/review" replace />}
        />
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
): "review" | "search" | "chat" | "admin" | undefined {
  if (pathname.startsWith("/kf/review") || pathname === "/kf") return "review";
  if (pathname.startsWith("/kf/search")) return "search";
  if (pathname.startsWith("/kf/chat")) return "chat";
  if (pathname.startsWith("/kf/admin")) return "admin";
  return undefined;
}

/** Top-bar tab labels → route paths. */
function routeForTopTab(tab: TopNavTab): string {
  switch (tab) {
    case "review": return "/kf/review";
    case "search": return "/kf/search";
    case "chat":   return "/kf/chat";
    case "admin":  return "/kf/admin";
  }
}

/**
 * Icon-rail tile → route path. Returns null for tiles that don't
 * navigate (the parent handles those, e.g. `settings` opens a modal).
 *
 * The mapping intentionally collapses several rail tiles onto the
 * existing routes:
 *   - activity → /kf/admin (admin hub holds the activity sparkline)
 *   - upload   → /kf/catalog (bulk-ops surface includes upload)
 *   - info     → /kf/review (the "current document" anchor)
 */
function routeForRailTile(tile: RailTileId): string | null {
  switch (tile) {
    case "activity": return "/kf/admin";
    case "upload":   return "/kf/catalog";
    case "review":   return "/kf/review";
    case "search":   return "/kf/search";
    case "info":     return "/kf/review";
    case "settings": return null; // handled separately — opens modal
  }
}

/**
 * Map URL pathname onto the icon-rail tile that should highlight as
 * "active". Falls back to `review` for the index + unknown routes.
 */
function pickActiveRail(
  pathname: string,
  tab: ReturnType<typeof pickActiveTab>,
): RailTileId {
  if (pathname.startsWith("/kf/catalog")) return "upload";
  if (pathname.startsWith("/kf/search")) return "search";
  if (pathname.startsWith("/kf/admin")) return "activity";
  if (pathname.startsWith("/kf/settings")) return "settings";
  if (tab === "chat") return "search"; // chat lives next to search on the rail
  return "review";
}

export default KnowledgeForgeApp;
