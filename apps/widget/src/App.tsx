import React, { useCallback, useState } from "react";

import { HealthCard } from "./sections/HealthCard";
import { DocumentsList } from "./sections/DocumentsList";
import { KnowledgeSummary } from "./sections/KnowledgeSummary";
import { UploadQueue } from "./sections/UploadQueue";
import { SettingsPanel } from "./settings/SettingsPanel";
import { getApiBaseUrl } from "./api/client";

const App: React.FC = () => {
  const [apiBaseUrl, setApiBaseUrl] = useState<string>(() => getApiBaseUrl());
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  // Bumped after each upload so child sections refresh.
  const [refreshTick, setRefreshTick] = useState<number>(0);

  const handleUploaded = useCallback(() => {
    setRefreshTick((n) => n + 1);
  }, []);

  const handleApiBaseUrlChange = useCallback((next: string) => {
    setApiBaseUrl(next);
    setRefreshTick((n) => n + 1);
  }, []);

  return (
    <div className="kw-widget">
      <header className="kw-widget__header">
        <div className="kw-widget__title">3DX KnowledgeForge</div>
        <button
          type="button"
          className="kw-widget__settings-btn"
          aria-label="Settings"
          onClick={() => setSettingsOpen((o) => !o)}
        >
          ⚙
        </button>
      </header>

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

      <main className="kw-widget__body">
        <HealthCard apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
        <UploadQueue apiBaseUrl={apiBaseUrl} onUploaded={handleUploaded} />
        <DocumentsList apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
        <KnowledgeSummary apiBaseUrl={apiBaseUrl} refreshTick={refreshTick} />
      </main>
    </div>
  );
};

export default App;
