import React, { useCallback, useEffect, useState } from "react";

import {
  SessionExpiredBanner,
  useSessionGuard,
} from "../../_shared/auth";
import {
  clearSessionTrigger,
  getApiBaseUrl,
  getHealth,
  getOrbitalUrl,
  setApiBaseUrl as persistApiBaseUrl,
  setSessionTrigger,
} from "./api/client";
import { Header } from "./components/Header";
import { SideRail, type ActiveMode } from "./components/SideRail";
import { ChatPanel } from "./sections/ChatPanel";
import { DocumentsList } from "./sections/DocumentsList";
import { HealthCard } from "./sections/HealthCard";
import { KnowledgeSummary } from "./sections/KnowledgeSummary";
import { SearchPanel } from "./sections/SearchPanel";
import { SettingsSection } from "./sections/SettingsSection";
import { UploadQueue } from "./sections/UploadQueue";

const HEALTH_POLL_MS = 30_000;

interface HealthSnapshot {
  ok: boolean;
  word: string;
  version?: string;
}

/**
 * Build the "open in Orbital" URL for a given document id.
 *
 * Why a helper instead of a one-liner: the previous implementation
 * (`${orbitalUrl.replace(/\/$/, "")}/?document=…`) appended a slash
 * before the query string. That works when `orbitalUrl` is the SPA
 * root (`https://example.com/orbital`), but breaks when it points
 * directly at a deployed `index.html` — the result becomes
 * `…/index.html/?document=…`, which to S3 looks like a request for
 * a key named `index.html/` (trailing slash) and returns 403
 * AccessDenied.
 *
 * This helper uses the URL API so:
 *   - `https://host/orbital`            → `https://host/orbital?document=X`
 *   - `https://host/orbital/`           → `https://host/orbital/?document=X`
 *   - `https://host/orbital/index.html` → `https://host/orbital/index.html?document=X`
 *
 * Exported so the widget's vitest can pin the behaviour without
 * mounting the full App.
 */
export function buildOpenInOrbitalUrl(
  orbitalUrl: string,
  documentId: string,
): string {
  try {
    const url = new URL(orbitalUrl);
    url.searchParams.set("document", documentId);
    return url.toString();
  } catch {
    // Fall back to the legacy concat pattern if the configured URL
    // isn't a parseable URL — keeps the widget useful even when an
    // operator typos the env var.
    const sep = orbitalUrl.includes("?") ? "&" : "?";
    return `${orbitalUrl}${sep}document=${encodeURIComponent(documentId)}`;
  }
}

const App: React.FC = () => {
  const [apiBaseUrl, setApiBaseUrl] = useState<string>(() => getApiBaseUrl());
  const [orbitalUrl] = useState<string>(() => getOrbitalUrl());
  const [activeMode, setActiveMode] = useState<ActiveMode>("docs");
  const [refreshTick, setRefreshTick] = useState<number>(0);
  const [uploadInFlight, setUploadInFlight] = useState<number>(0);
  const [health, setHealth] = useState<HealthSnapshot>({ ok: false, word: "checking" });
  // Cross-section navigation target — set when a chat citation or
  // search result is clicked. ``DocumentsList`` consumes this and
  // flashes the matching row.
  const [highlightDocId, setHighlightDocId] = useState<string | null>(null);

  // Session-expired wiring (#83 slice 3 / ADR-019 §5). The provider
  // sits at the widget root in index.tsx; here we just register the
  // trigger so the shared ApiError class can flip the banner on for
  // any 401, regardless of which section fired the request.
  const session = useSessionGuard();
  useEffect(() => {
    setSessionTrigger(session.trigger);
    return () => {
      clearSessionTrigger();
    };
  }, [session.trigger]);

  // Dev stub: ``KW_AUTH_MODE=dev`` (default per #245) never returns
  // 401, so the banner is unreachable through normal interaction in a
  // demo build. Loading the widget with ``#force-session-expired`` in
  // the URL hash flips it once for visual review. Removed once
  // bearer mode is the default and real 401s show up organically.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.location.hash === "#force-session-expired") {
      session.trigger();
    }
  }, [session]);

  // 3DX context: the widget runs as a tile inside 3DDashboard, so
  // ``window.location.reload()`` reloads the tile and re-fires the
  // host's auth handshake. Same call as web/explorer until a refresh-
  // token flow lands (ADR-019 follow-up slice).
  const handleSignInAgain = useCallback(() => {
    if (typeof window !== "undefined") window.location.reload();
  }, []);

  // Lightweight, header-level health probe so the live pill in the
  // header and the rail status dot reflect reachability regardless of
  // which mode the user is on. The Health card runs its own probe
  // for the in-card detail; the duplication is fine — both hit a
  // tiny endpoint and the call sites are independent.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const h = await getHealth({ baseUrl: apiBaseUrl, signal: controller.signal });
        if (!cancelled) setHealth({ ok: true, word: h.status, version: h.version });
      } catch {
        if (!cancelled) setHealth({ ok: false, word: "unreachable" });
      }
    };
    void poll();
    const interval = window.setInterval(poll, HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [apiBaseUrl, refreshTick]);

  const handleUploaded = useCallback(() => {
    setRefreshTick((n) => n + 1);
  }, []);

  const handleApiBaseUrlChange = useCallback((next: string) => {
    persistApiBaseUrl(next);
    setApiBaseUrl(next);
    setRefreshTick((n) => n + 1);
  }, []);

  const handleRefresh = useCallback(() => {
    setRefreshTick((n) => n + 1);
  }, []);

  const jumpToDocument = useCallback((documentId: string) => {
    setHighlightDocId(documentId);
    setActiveMode("docs");
  }, []);

  // The header gear button now toggles the dedicated settings mode
  // rather than opening the legacy overlay. Toggling means: clicking
  // again returns to whatever mode the user was on, so the gear is
  // a true bookmark.
  const lastNonSettingsMode = React.useRef<ActiveMode>("docs");
  if (activeMode !== "settings") lastNonSettingsMode.current = activeMode;
  const toggleSettings = useCallback(() => {
    setActiveMode((current) =>
      current === "settings" ? lastNonSettingsMode.current : "settings",
    );
  }, []);

  return (
    <div className="kw-widget">
      <SessionExpiredBanner
        visible={session.expired}
        onSignIn={handleSignInAgain}
        className="kw-widget__session-expired"
      />
      <Header
        health={health}
        settingsOpen={activeMode === "settings"}
        orbitalUrl={orbitalUrl}
        onToggleSettings={toggleSettings}
        onRefresh={handleRefresh}
      />

      <div className="kw-body">
        <SideRail
          active={activeMode}
          onChange={setActiveMode}
          uploadInFlight={uploadInFlight}
          healthOk={health.ok}
        />
        <main className="kw-main">
          {activeMode === "health" && (
            <HealthCard apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
          )}
          {activeMode === "upload" && (
            <UploadQueue
              apiBaseUrl={apiBaseUrl}
              onUploaded={handleUploaded}
              onInFlightChange={setUploadInFlight}
            />
          )}
          {activeMode === "docs" && (
            <DocumentsList
              apiBaseUrl={apiBaseUrl}
              refreshTick={refreshTick}
              highlightDocumentId={highlightDocId}
              onOpenDocument={(doc) => {
                window.open(
                  buildOpenInOrbitalUrl(orbitalUrl, doc.id),
                  "_blank",
                  "noopener,noreferrer",
                );
              }}
            />
          )}
          {activeMode === "search" && (
            <SearchPanel
              apiBaseUrl={apiBaseUrl}
              refreshTick={refreshTick}
              onSelectResult={(result) => jumpToDocument(result.document_id)}
            />
          )}
          {activeMode === "chat" && (
            <ChatPanel
              apiBaseUrl={apiBaseUrl}
              refreshTick={refreshTick}
              onSelectCitation={(citation) => jumpToDocument(citation.document_id)}
            />
          )}
          {activeMode === "kg" && (
            <KnowledgeSummary apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
          )}
          {activeMode === "settings" && (
            <SettingsSection
              apiBaseUrl={apiBaseUrl}
              refreshTick={refreshTick}
              onApiBaseUrlChange={handleApiBaseUrlChange}
            />
          )}
        </main>
      </div>
    </div>
  );
};

export default App;
