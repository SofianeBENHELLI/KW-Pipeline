/**
 * 3DX Knowledge Explorer — root tile.
 *
 * Three modes wired through the side rail: Browse / Document / Graph.
 * Picking a document in Browse switches to Document; the Document
 * toolbar offers a "Graph" button that switches to Graph scoped to
 * that document; Graph nodes/edges that resolve back to a document
 * id can switch to Document. The user is never further than one
 * keypress from any of the three surfaces.
 *
 * State that doesn't need to round-trip through React for every
 * interaction (selected document id, refresh tick) lives here so the
 * three sections can stay as pure children of this shell.
 */

import React, { useCallback, useEffect, useState } from "react";

import { getApiBaseUrl, getHealth } from "./api/client";
import { Header } from "./components/Header";
import { SideRail, type ActiveMode } from "./components/SideRail";
import { BrowseSection } from "./sections/BrowseSection";
import { DocumentSection } from "./sections/DocumentSection";
import { GraphSection } from "./sections/GraphSection";
import { SettingsPanel } from "./settings/SettingsPanel";

const HEALTH_POLL_MS = 30_000;

interface HealthSnapshot {
  ok: boolean;
  word: string;
  version?: string;
}

const App: React.FC = () => {
  const [apiBaseUrl, setApiBaseUrl] = useState<string>(() => getApiBaseUrl());
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [activeMode, setActiveMode] = useState<ActiveMode>("browse");
  const [refreshTick, setRefreshTick] = useState<number>(0);
  const [health, setHealth] = useState<HealthSnapshot>({ ok: false, word: "checking" });
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);

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

  const handleApiBaseUrlChange = useCallback((next: string) => {
    setApiBaseUrl(next);
    setRefreshTick((n) => n + 1);
  }, []);

  const handleRefresh = useCallback(() => {
    setRefreshTick((n) => n + 1);
  }, []);

  const handlePickDocument = useCallback((id: string) => {
    setSelectedDocumentId(id);
    setActiveMode("document");
  }, []);

  const handleBackToBrowse = useCallback(() => {
    setActiveMode("browse");
  }, []);

  const handleOpenGraph = useCallback(() => {
    setActiveMode("graph");
  }, []);

  return (
    <div className="kw-widget">
      <Header
        health={health}
        settingsOpen={settingsOpen}
        onToggleSettings={() => setSettingsOpen((o) => !o)}
        onRefresh={handleRefresh}
      />

      {settingsOpen && (
        <SettingsPanel
          initialValue={apiBaseUrl}
          onSave={(next) => {
            handleApiBaseUrlChange(next);
            setSettingsOpen(false);
          }}
          onCancel={() => setSettingsOpen(false)}
        />
      )}

      <div className="kw-body">
        <SideRail
          active={activeMode}
          onChange={setActiveMode}
          healthOk={health.ok}
          documentDisabled={selectedDocumentId === null}
        />
        <main className="kw-main">
          {activeMode === "browse" && (
            <BrowseSection
              apiBaseUrl={apiBaseUrl}
              refreshTick={refreshTick}
              onPickDocument={handlePickDocument}
            />
          )}
          {activeMode === "document" && selectedDocumentId !== null && (
            <DocumentSection
              apiBaseUrl={apiBaseUrl}
              documentId={selectedDocumentId}
              refreshTick={refreshTick}
              onBack={handleBackToBrowse}
              onOpenGraph={handleOpenGraph}
            />
          )}
          {activeMode === "graph" && (
            <GraphSection
              apiBaseUrl={apiBaseUrl}
              refreshTick={refreshTick}
              documentId={selectedDocumentId}
              onOpenDocument={handlePickDocument}
            />
          )}
        </main>
      </div>
    </div>
  );
};

export default App;
