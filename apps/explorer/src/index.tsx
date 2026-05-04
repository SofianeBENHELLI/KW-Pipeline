import React from "react";
import { createRoot } from "react-dom/client";
import { widget, disableDefaultCSS } from "@widget-lab/3ddashboard-utils";

import App from "./App";
import "./styles.css";

const start = (): void => {
  disableDefaultCSS(true);
  widget.setTitle("3DX Knowledge Explorer");

  const rootElement = document.getElementById("root");
  if (rootElement === null) throw new Error("Failed to find the root element");
  const root = createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
};

widget.addEvent("onLoad", () => {
  start();
});
