# apps/_shared

Code shared by the three frontend apps (`apps/web`, `apps/widget`,
`apps/explorer`).

## Why

The audit on 2026-05-04 (`docs/quality/2026-05-04-code-audit.md`)
flagged 12 of 71 frontend findings collapsing into one
recommendation: *make a shared package*. ApiError, the envelope
parser, status-badge logic, icon registries, and the
`DocumentVersionStatus` literal were each reimplemented 2–3 times,
so a fix to one path silently missed the other apps.

This directory is that shared package. It is **not** a published npm
package today — every frontend imports from a relative path
(`apps/widget/src/api/client.ts` does
`from "../../../_shared/api-core/ApiError"`). The relative-path
shape is deliberate: every existing toolchain (Vite for `apps/web`,
Webpack for `apps/widget` / `apps/explorer`) resolves relative
imports without any config change. If the package outgrows that
shape, a `tsconfig.base.json` path alias + matching `webpack.alias`
is the obvious next step.

## Layout

```
apps/_shared/
├── README.md
└── api-core/                # Public error envelope handling
    ├── ApiError.ts          # ApiError class + asApiError parser
    └── index.ts             # Public exports
```

Future slices of audit P0 #227 will extend this with `ui/StatusBadge`,
`ui/icons`, `domain/document`, `domain/graph`, `format/*` helpers,
and the OpenAPI-typescript codegen output for `api-types/`.

## How to add a new module

1. Create the module file under an appropriate sub-directory (or a
   new sub-directory for a new theme).
2. Add an `index.ts` that re-exports the public surface from that
   sub-directory.
3. Update consumers' imports — keep paths relative to avoid
   bundler-config divergence between the three apps.

## What lives here vs in the apps

- **Lives here:** anything 2+ apps need to keep in lock-step. The
  envelope parser is the canonical example — a backend change to
  the error shape needs one fix, not three.
- **Stays in the app:** anything app-specific. App-specific routing,
  feature flags, layout, and feature components stay where they
  are. The shared package is a flat-file utility belt, not a
  framework.

## Why no `package.json`

A workspace package would let us declare a real public API, run
package-level tests, and version the surface. We opted not to do
that yet because:

- The current bundlers diverge across the three apps (Vite +
  Webpack); adding a workspace package adds more tooling alignment
  cost than it removes today.
- The package's surface is small enough (a handful of utilities)
  that drift is unlikely between the first slice and the next one.

When the package surface grows enough that we need versioned
contracts (e.g. once `api-types/` is generated), promote it to a
real workspace.
