/**
 * RawExtractionTabs — three-tab `extraction.json` / `page-spans` /
 * `tables` viewer for the Review tab.
 *
 * Renders cached extraction returned by `useExtraction`. Each tab is a
 * mono `<pre>` block scoped under `kf-code`. The "spans" tab projects
 * the JSON's per-page section list; "tables" filters to nodes flagged
 * `kind === "table"`. When the extraction is absent (404), surfaces a
 * simple "No extraction yet" panel.
 */

import { useState } from "react";
import type { ReactElement } from "react";

import { Card, CardHead, SectionH } from "../index";
import type { ApiRawExtraction, ApiRawSection } from "../../api/types";
import type { ExtractionStatus } from "../hooks/useExtraction";

export type ExtractionTab = "json" | "spans" | "tables";

export interface RawExtractionTabsProps {
  status: ExtractionStatus;
  extraction: ApiRawExtraction | null;
  errorMessage?: string | null;
}

export function RawExtractionTabs({
  status,
  extraction,
  errorMessage,
}: RawExtractionTabsProps): ReactElement {
  const [tab, setTab] = useState<ExtractionTab>("json");

  return (
    <Card>
      <CardHead right={<TabStrip tab={tab} setTab={setTab} />}>
        <SectionH>Raw extraction</SectionH>
      </CardHead>
      <Body
        status={status}
        extraction={extraction}
        errorMessage={errorMessage}
        tab={tab}
      />
    </Card>
  );
}

function TabStrip({
  tab,
  setTab,
}: {
  tab: ExtractionTab;
  setTab: (t: ExtractionTab) => void;
}): ReactElement {
  const opts: Array<{ id: ExtractionTab; label: string }> = [
    { id: "json", label: "extraction.json" },
    { id: "spans", label: "page-spans" },
    { id: "tables", label: "tables" },
  ];
  return (
    <div className="kf-tabstrip" role="tablist" aria-label="Raw extraction tabs">
      {opts.map((o) => (
        <button
          key={o.id}
          type="button"
          role="tab"
          aria-selected={tab === o.id}
          className={`kf-tab ${tab === o.id ? "is-active" : ""}`}
          onClick={() => setTab(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Body({
  status,
  extraction,
  errorMessage,
  tab,
}: {
  status: ExtractionStatus;
  extraction: ApiRawExtraction | null;
  errorMessage?: string | null;
  tab: ExtractionTab;
}): ReactElement {
  if (status === "loading") {
    return <div className="kf-code__placeholder">Loading extraction…</div>;
  }
  if (status === "error") {
    return (
      <div className="kf-code__placeholder kf-code__placeholder--err" role="alert">
        Failed to load extraction
        {errorMessage ? <>: <code>{errorMessage}</code></> : null}
      </div>
    );
  }
  if (status === "absent" || !extraction) {
    return (
      <div className="kf-code__placeholder">
        No extraction yet — run Extract on the version above.
      </div>
    );
  }

  if (tab === "json") {
    return (
      <pre className="kf-code orb-mono orb-scroll" data-testid="kf-extr-json">
        {JSON.stringify(extraction, null, 2)}
      </pre>
    );
  }
  if (tab === "spans") {
    const sections = (extraction.sections ?? []) as ApiRawSection[];
    if (sections.length === 0) {
      return (
        <div className="kf-code__placeholder">
          No page-span data in this extraction.
        </div>
      );
    }
    return (
      <pre className="kf-code orb-mono orb-scroll" data-testid="kf-extr-spans">
        {JSON.stringify(sections, null, 2)}
      </pre>
    );
  }
  // tables — filter sections whose path/kind hints "table"
  const tables = ((extraction.sections ?? []) as ApiRawSection[]).filter(
    (s) =>
      typeof s.heading === "string" &&
      /table/i.test(s.heading),
  );
  return (
    <pre className="kf-code orb-mono orb-scroll" data-testid="kf-extr-tables">
      {tables.length === 0
        ? "No tables detected in this extraction."
        : JSON.stringify(tables, null, 2)}
    </pre>
  );
}
