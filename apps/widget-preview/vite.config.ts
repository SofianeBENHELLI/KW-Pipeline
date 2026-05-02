import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
// Real widget source — sibling directory in the same repo.
const widgetSrc = resolve(here, "../widget/src");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // The 3DDashboard runtime utilities only exist inside 3DEXPERIENCE.
      // Replace the import with a local no-op shim so the widget mounts
      // in a plain browser tab.
      "@widget-lab/3ddashboard-utils": resolve(here, "widget-stub.ts"),
      "@kw-widget": widgetSrc,
    },
  },
  server: {
    fs: {
      // Allow Vite to read the sibling `apps/widget/src` directory.
      allow: [here, widgetSrc, resolve(here, "..")],
    },
  },
});
