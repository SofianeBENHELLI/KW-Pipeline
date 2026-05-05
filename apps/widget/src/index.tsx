import React from "react";
import { createRoot } from "react-dom/client";
import { widget, disableDefaultCSS } from "@widget-lab/3ddashboard-utils";

import { SessionGuardProvider } from "../../_shared/auth";

import App from "./App";
import "./styles.css";

const start = (): void => {
  disableDefaultCSS(true);
  widget.setTitle("3DX KnowledgeForge");

  const rootElement = document.getElementById("root");
  if (rootElement === null) throw new Error("Failed to find the root element");
  const root = createRoot(rootElement);
  // SessionGuardProvider wraps the entire widget tree so any nested
  // section (DocumentsList, ChatPanel, …) hitting a 401 flips the
  // shared banner via ``setSessionTrigger`` (#83 slice 3 / ADR-019 §5).
  root.render(
    <React.StrictMode>
      <SessionGuardProvider>
        <App />
      </SessionGuardProvider>
    </React.StrictMode>,
  );
};

widget.addEvent("onLoad", () => {
  start();
});
