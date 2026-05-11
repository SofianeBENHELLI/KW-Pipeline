import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { SessionGuardProvider } from "../../_shared/auth";

import App from "./App";

import "./styles/tokens.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Failed to find #root element");
}

// SessionGuardProvider sits above BrowserRouter so any nested route
// can call into ``useSessionGuard`` (#83 slice 3 / ADR-019 §5).
createRoot(container).render(
  <StrictMode>
    <SessionGuardProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </SessionGuardProvider>
  </StrictMode>,
);
