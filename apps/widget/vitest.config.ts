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

// Absolute paths to this app's own ``node_modules`` packages —
// shared-package tests live in ``apps/_shared/`` (which has no
// node_modules of its own), so we redirect bare specifiers like
// ``@testing-library/react`` to the widget's installed copy.
// Without this, Vite's import-analysis fails to resolve the import
// when it runs a test file from outside the project root.
const widgetNodeModule = (pkg: string): string =>
  fileURLToPath(new URL(`./node_modules/${pkg}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@widget-lab/3ddashboard-utils": widgetStub,
      "@testing-library/react": widgetNodeModule("@testing-library/react"),
      "@testing-library/jest-dom": widgetNodeModule(
        "@testing-library/jest-dom",
      ),
    },
  },
  // Allow Vite to read files outside ``apps/widget/`` so the bundler
  // can serve ``apps/_shared/**`` test files when ``test.include``
  // pulls them in. Without this, Vite refuses with "Failed to load url
  // /…/_shared/…" because the path is outside its default fs.allow
  // root.
  server: {
    fs: {
      allow: [fileURLToPath(new URL("../..", import.meta.url))],
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
    // Pick up the shared package's own tests so changes to
    // ``apps/_shared/settings-hub`` are validated alongside the widget.
    // The shared package has no standalone test runner today.
    include: [
      "src/**/*.{test,spec}.{ts,tsx}",
      "../_shared/**/*.{test,spec}.{ts,tsx}",
    ],
  },
});
