/// <reference types="vitest" />
//
// Vitest config for apps/widget (audit P0 #230 first slice).
//
// The widget builds with webpack at production time but tests run
// directly via Vitest (which uses Vite under the hood for transforms).
// React components have no webpack-only loaders, so this works.
//
// ``@widget-lab/3ddashboard-utils`` is a file-linked dep that only
// exists inside the 3DEXPERIENCE host runtime. Tests run outside that
// host, so we alias the import to the existing browser-side stub at
// ``apps/widget-preview/widget-stub.ts`` — same shim the local dev
// preview uses.

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const widgetStub = fileURLToPath(
  new URL("../widget-preview/widget-stub.ts", import.meta.url),
);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@widget-lab/3ddashboard-utils": widgetStub,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
