/**
 * CatalogView — the page rendered at `/kf/catalog`. Wires the
 * `useDocuments` hook to a `<CatalogTable>` and stacks the system
 * banners on top.
 *
 * The catalog is intentionally separate from the Review Workspace's
 * rail: this is the bulk-ops surface (column toggles, multi-select
 * header checkbox, sticky bulk action bar). Per design §4.
 */

import { useState } from "react";
import type { ReactElement } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { BannerStack } from "./Banners";
import { CatalogTable } from "./CatalogTable";
import "./catalog.css";
import { useDocuments, type RailView } from "../hooks/useDocuments";

const VALID_VIEWS = new Set<RailView>(["recent", "review", "validated", "failed"]);

function parseView(raw: string | null): RailView {
  if (raw && VALID_VIEWS.has(raw as RailView)) return raw as RailView;
  return "recent";
}

export function CatalogView(): ReactElement {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const view = parseView(searchParams.get("view"));
  const query = searchParams.get("q") ?? "";
  // PR 5 doesn't yet expose a banner-driving force-auto config call —
  // a follow-up will read `/admin/config` once and wire the flag.
  // Keeping the prop wired so downstream PRs only need to swap the
  // boolean source.
  const [forceAutoOn] = useState(false);

  const live = useDocuments({ view, q: query, limit: 100 });

  return (
    <section className="kf-catpage" aria-label="Knowledge Forge — Catalog">
      <BannerStack forceAutoOn={forceAutoOn} forceAutoFlaggedCount={0} />
      <header className="kf-catpage__head">
        <h1 className="kf-catpage__title">Catalog</h1>
        <p className="kf-catpage__subtitle">
          Bulk operations over the document corpus. Use the rail in the
          Review Workspace for single-doc review.
        </p>
      </header>
      <CatalogTable
        documents={live.items}
        loading={live.status === "loading"}
        errorMessage={
          live.status === "error"
            ? (live.error?.message ?? "Failed to load catalog")
            : null
        }
        onOpen={(docId) => {
          const search = searchParams.toString();
          navigate(
            `/kf/review/${docId}${search ? `?${search}` : ""}`,
            { replace: false },
          );
        }}
      />
    </section>
  );
}
