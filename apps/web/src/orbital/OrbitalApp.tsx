import { useCallback, useEffect, useState } from "react";

import { SessionExpiredBanner, useSessionGuard } from "../../../_shared/auth";
import { getApiBaseUrl } from "../api/client";
import { useAdminConfig } from "../api/useAdminConfig";

import { AdminPage } from "./AdminPage";
import { CatalogScreen } from "./CatalogScreen";
import { ChatPage } from "./ChatPage";
import { GraphPage } from "./GraphPage";
import { SearchPage } from "./SearchPage";
import { SettingsModal } from "./SettingsModal";
import { TopBar, type OrbNavId } from "./TopBar";
import { Workspace } from "./Workspace";

import "./tokens.css";
import "./styles.css";

/**
 * `/orb` entry. Renders a single top bar over a screen that depends on
 * the active nav tab + whether a document is selected:
 *
 *   - nav=review  + no doc        → CatalogScreen
 *   - nav=review  + doc selected  → Workspace (rail + dochead + tabs)
 *   - nav=graph                   → GraphPage (scoped to selected doc if any)
 *   - nav=search                  → SearchPage
 *   - nav=chat                    → ChatPage
 *   - nav=admin                   → AdminPage
 *
 * The `?document=<id>` query param deep-links straight to the workspace
 * (used by Forge's "open in Orbital" link).
 */
export function OrbitalApp() {
  const [nav, setNav] = useState<OrbNavId>("review");
  const [selectedId, setSelectedId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const params = new URLSearchParams(window.location.search);
    return params.get("document");
  });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [missingDocId, setMissingDocId] = useState<string | null>(null);
  const admin = useAdminConfig(getApiBaseUrl());
  const forceAutoActive = !!admin.config?.hitl.force_auto_corpus;

  // Keep ?document=… in sync with selection so a refresh stays put.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (selectedId) url.searchParams.set("document", selectedId);
    else url.searchParams.delete("document");
    window.history.replaceState(null, "", url.toString());
  }, [selectedId]);

  const openDoc = useCallback((id: string) => {
    setSelectedId(id);
    setNav("review");
  }, []);

  const backToCatalog = useCallback(() => {
    setSelectedId(null);
    setNav("review");
  }, []);

  const onNav = useCallback((next: OrbNavId) => {
    setNav(next);
  }, []);

  return (
    <div style={{ position: "fixed", inset: 0, display: "grid", gridTemplateRows: "44px 1fr", minHeight: 0, overflow: "hidden" }}>
      <TopBar
        activeNav={nav}
        onNav={onNav}
        onOpenSettings={() => setSettingsOpen(true)}
        onClickBrand={backToCatalog}
      />
      <div style={{ minHeight: 0, overflow: "hidden" }}>
        <SessionBannerHost />
        {nav === "review" && !selectedId && (
          <CatalogScreen
            onOpenDocument={openDoc}
            forceAutoActive={forceAutoActive}
            deepLinkMissing={
              missingDocId
                ? { id: missingDocId, onDismiss: () => setMissingDocId(null) }
                : null
            }
          />
        )}
        {nav === "review" && selectedId && (
          <Workspace
            initialDocumentId={selectedId}
            onBackToCatalog={backToCatalog}
            onDocumentMissing={(id) => {
              setMissingDocId(id);
              setSelectedId(null);
              setNav("review");
            }}
          />
        )}
        {nav === "graph" && (
          <GraphPage documentId={selectedId} onOpenDocument={openDoc} />
        )}
        {nav === "search" && <SearchPage onOpenDocument={openDoc} onClose={() => setNav("review")} />}
        {nav === "chat" && <ChatPage onOpenDocument={openDoc} onClose={() => setNav("review")} />}
        {nav === "admin" && <AdminPage onClose={() => setNav("review")} />}
      </div>
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

function SessionBannerHost() {
  const session = useSessionGuard();
  return (
    <SessionExpiredBanner
      visible={session.expired}
      onSignIn={() => {
        session.reset();
        window.location.reload();
      }}
    />
  );
}
