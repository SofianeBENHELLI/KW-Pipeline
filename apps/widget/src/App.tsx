import React, { useCallback, useEffect, useState } from "react";

import { getApiBaseUrl, getHealth, setApiBaseUrl as persistApiBaseUrl } from "./api/client";
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

const App: React.FC = () => {
  const [apiBaseUrl, setApiBaseUrl] = useState<string>(() => getApiBaseUrl());
  const [activeMode, setActiveMode] = useState<ActiveMode>("docs");
  const [refreshTick, setRefreshTick] = useState<number>(0);
  const [uploadInFlight, setUploadInFlight] = useState<number>(0);
  const [health, setHealth] = useState<HealthSnapshot>({ ok: false, word: "checking" });
  // Cross-section navigation target — set when a chat citation or
  // search result is clicked. ``DocumentsList`` consumes this and
  // flashes the matching row.
  const [highlightDocId, setHighlightDocId] = useState<string | null>(null);

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
      <Header
        health={health}
        settingsOpen={activeMode === "settings"}
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
