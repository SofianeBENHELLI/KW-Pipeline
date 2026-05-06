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

// Absolute paths to this app's own ``node_modules`` packages —
// shared-package tests live in ``apps/_shared/`` (which has no
// node_modules of its own), so we redirect bare specifiers like
// ``@testing-library/react`` to the explorer's installed copy.
const explorerNodeModule = (pkg: string): string =>
  fileURLToPath(new URL(`./node_modules/${pkg}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@widget-lab/3ddashboard-utils": widgetStub,
      "@testing-library/react": explorerNodeModule("@testing-library/react"),
      "@testing-library/jest-dom": explorerNodeModule(
        "@testing-library/jest-dom",
      ),
    },
  },
  // Allow Vite to read files outside ``apps/explorer/`` so the bundler
  // can serve ``apps/_shared/**`` test files when ``test.include`` pulls
  // them in. Without this, Vite refuses with "Failed to load url
  // /…/_shared/…" because the path is outside its default fs.allow root.
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
    // ``apps/_shared/demo-toggle`` (and friends) are validated alongside
    // the explorer. The shared package has no standalone test runner.
    include: [
      "src/**/*.{test,spec}.{ts,tsx}",
      "../_shared/**/*.{test,spec}.{ts,tsx}",
    ],
  },
});
