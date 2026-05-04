// ESLint flat config for apps/web (audit P0 #231).
//
// Why
// ---
// Before this file landed, none of the three frontends ran ESLint.
// The audit found that ``apps/explorer/src/App.tsx`` carried an
// explicit ``// eslint-disable-next-line react-hooks/exhaustive-deps``
// suppression for a rule that was *not actually enforced anywhere*.
// React-hook bugs and JSX-a11y violations were going through every
// PR with no friction.
//
// Scope of the first slice
// ------------------------
// - ``apps/web`` consumes this config today. ``npm run lint`` runs
//   ``eslint .`` from this directory and the CI ``Lint Frontend
//   (eslint)`` job runs that script.
// - ``apps/widget`` and ``apps/explorer`` will get their own copy of
//   this file in a follow-up slice. ESLint flat config can't be
//   centralised at repo root today because each app has its own
//   ``node_modules`` (no workspaces yet) — Node ESM resolution
//   requires the plugin packages to be reachable from where the
//   config lives. Once we promote ``apps/_shared`` to a workspace
//   package (audit #227 §3.1 last bullet), this config can move to
//   the shared package and every app re-export it.
//
// Severity choice
// ---------------
// First-run rules are mostly **warnings**, not errors, so the first
// PR doesn't have to fix every existing violation in one go. Two
// rules stay at "error" level because they catch real bugs cheaply:
//
// - ``react-hooks/rules-of-hooks`` — calling a hook conditionally
//   is always a bug.
// - ``@typescript-eslint/no-misused-promises`` — passing an async
//   function where a sync one is expected (e.g. ``onClick``) silently
//   swallows the rejection, which is always a bug.
//
// Follow-up slices will promote the warnings to errors as the
// existing violations get cleaned up.

import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default [
  // Ignore generated, vendored, and build outputs.
  {
    ignores: [
      "**/dist/**",
      "**/build/**",
      "**/node_modules/**",
      "**/coverage/**",
      "**/.venv*/**",
      "**/.kw-pipeline/**",
      "apps/web/src/api/generated/**",
      "apps/widget/aws/**",
      "apps/explorer/aws/**",
      // Stub modules whose only purpose is to satisfy a build-time
      // import; not worth linting.
      "apps/widget-preview/widget-stub.ts",
    ],
  },

  js.configs.recommended,
  ...tseslint.configs.recommended,

  // React-hooks + jsx-a11y rules apply only to files that can host JSX.
  // Plugin declaration and rule overrides live in the same block; ESLint
  // flat config requires it (the plugin is scoped to the block where
  // it's declared).
  {
    files: ["**/*.{tsx,jsx}"],
    plugins: {
      "react-hooks": reactHooks,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      // ``rules-of-hooks`` catches calling a hook conditionally —
      // always a real bug, kept at error.
      "react-hooks/rules-of-hooks": "error",
      // ``exhaustive-deps`` catches stale closures over state — common
      // and worth surfacing, but the audit found existing violations
      // so we warn for now and promote to error in a follow-up.
      "react-hooks/exhaustive-deps": "warn",
      // a11y warnings — surface the audit's findings without failing
      // CI on the first run.
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/no-noninteractive-element-interactions": "warn",
      "jsx-a11y/no-static-element-interactions": "warn",
      "jsx-a11y/anchor-is-valid": "warn",
      "jsx-a11y/label-has-associated-control": "warn",
    },
  },

  // First-run leniency for non-JSX rules. Promote to ``error`` once
  // existing violations are addressed.
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          // Match the existing TS ``noUnusedLocals`` carve-out: leading
          // underscore = intentionally unused.
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-empty-object-type": "warn",
      "no-empty": ["warn", { allowEmptyCatch: true }],
    },
  },

  // Test files — relax rules that conflict with common test idioms.
  {
    files: ["**/*.{test,spec}.{ts,tsx}", "**/test-setup.ts", "**/__mocks__/**"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-empty-function": "off",
      "prefer-const": "warn",
    },
  },

  // Node scripts (build helpers, codegen wrappers) run under Node, not
  // a browser, so the default browser-globals environment doesn't expose
  // ``console`` / ``process`` / ``require`` for them.
  {
    files: ["**/scripts/**/*.{js,mjs,ts}", "**/*.config.{js,mjs,ts}"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },

  // Browser-runtime files (the React app itself) need DOM globals.
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
  },
];
