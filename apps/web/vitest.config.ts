/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Absolute paths to this app's own ``node_modules`` packages —
// shared-package tests live in ``apps/_shared/`` (which has no
// node_modules of its own), so we redirect bare specifiers like
// ``@testing-library/react`` to the web app's installed copy.
const webNodeModule = (pkg: string): string =>
  fileURLToPath(new URL(`./node_modules/${pkg}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@testing-library/react": webNodeModule("@testing-library/react"),
      "@testing-library/jest-dom": webNodeModule("@testing-library/jest-dom"),
    },
  },
  // Allow Vite to read files outside ``apps/web/`` so the bundler can
  // serve ``apps/_shared/**`` test files when ``test.include`` pulls
  // them in.
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
    // the web app. The shared package has no standalone test runner.
    include: [
      "src/**/*.{test,spec}.{ts,tsx}",
      "../_shared/**/*.{test,spec}.{ts,tsx}",
    ],
  },
});
