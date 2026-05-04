/// <reference types="vitest" />
//
// Vitest config for apps/explorer (audit P0 #230 first slice).
//
// The explorer builds with webpack at production time but tests run
// directly via Vitest. ``@widget-lab/3ddashboard-utils`` is aliased
// to the in-repo browser-side stub at
// ``apps/widget-preview/widget-stub.ts``, same as apps/widget.

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
