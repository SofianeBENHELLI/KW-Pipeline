import React, { useCallback, useEffect, useState } from "react";

import { getApiBaseUrl, getHealth } from "./api/client";
import { Header } from "./components/Header";
import { SideRail, type ActiveMode } from "./components/SideRail";
import { DocumentsList } from "./sections/DocumentsList";
import { HealthCard } from "./sections/HealthCard";
import { KnowledgeSummary } from "./sections/KnowledgeSummary";
import { UploadQueue } from "./sections/UploadQueue";
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
  const [activeMode, setActiveMode] = useState<ActiveMode>("docs");
  const [refreshTick, setRefreshTick] = useState<number>(0);
  const [uploadInFlight, setUploadInFlight] = useState<number>(0);
  const [health, setHealth] = useState<HealthSnapshot>({ ok: false, word: "checking" });

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
    setApiBaseUrl(next);
    setRefreshTick((n) => n + 1);
  }, []);

  const handleRefresh = useCallback(() => {
    setRefreshTick((n) => n + 1);
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
            <DocumentsList apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
          )}
          {activeMode === "kg" && (
            <KnowledgeSummary apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
          )}
        </main>
      </div>
    </div>
  );
};

export default App;
