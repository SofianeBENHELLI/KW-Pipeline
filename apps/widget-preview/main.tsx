/**
 * Standalone preview entry. Mounts the actual widget App from
 * `apps/widget/src` (resolved via the `@kw-widget` Vite alias) into a
 * single fluid root. Resize the browser window to see how the widget
 * reflows — same model as the 3DDashboard host resizing its tile.
 */

import React from "react";
import { createRoot } from "react-dom/client";

// @ts-expect-error — virtual alias resolved by Vite, not TS.
import App from "@kw-widget/App";
// @ts-expect-error — virtual alias resolved by Vite, not TS.
import "@kw-widget/styles.css";

const el = document.getElementById("root");
if (el) {
  createRoot(el).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
