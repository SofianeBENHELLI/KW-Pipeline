/**
 * SemanticMarkdownCard — three-tab preview / source / diff viewer for
 * the Review tab.
 *
 *   preview  — the Markdown rendered as fenced text inside a fade-out
 *              `<pre>` (no MD-to-HTML conversion in PR 4 — the
 *              prototype rendered raw text and that's the truth)
 *   source   — the underlying SemanticDocument JSON
 *   diff     — placeholder; PR 4.x will wire a real diff vs v{n-1}
 */

import { useState } from "react";
import type { ReactElement } from "react";

import { Card, CardHead, SectionH } from "../index";
import type { ApiSemanticDocument } from "../../api/types";
import type { SemanticStatus } from "../hooks/useSemantic";

export type SemanticTab = "preview" | "source" | "diff";

export interface SemanticMarkdownCardProps {
  status: SemanticStatus;
  semantic: ApiSemanticDocument | null;
  markdown: string | null;
  errorMessage?: string | null;
  /** Previous version number — only used to label the "diff" tab. */
  previousVersion?: number | null;
}

export function SemanticMarkdownCard({
  status,
  semantic,
  markdown,
  errorMessage,
  previousVersion,
}: SemanticMarkdownCardProps): ReactElement {
  const [tab, setTab] = useState<SemanticTab>("preview");
  const opts: Array<{ id: SemanticTab; label: string }> = [
    { id: "preview", label: "preview" },
    { id: "source", label: "source" },
    {
      id: "diff",
      label: previousVersion ? `diff vs v${previousVersion}` : "diff",
    },
  ];

  return (
    <Card>
      <CardHead
        right={
          <div className="kf-tabstrip" role="tablist" aria-label="Semantic tabs">
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
        }
      >
        <SectionH>Semantic markdown</SectionH>
      </CardHead>
      <Body
        status={status}
        semantic={semantic}
        markdown={markdown}
        errorMessage={errorMessage}
        tab={tab}
      />
    </Card>
  );
}

function Body({
  status,
  semantic,
  markdown,
  errorMessage,
  tab,
}: {
  status: SemanticStatus;
  semantic: ApiSemanticDocument | null;
  markdown: string | null;
  errorMessage?: string | null;
  tab: SemanticTab;
}): ReactElement {
  if (status === "loading") {
    return <div className="kf-code__placeholder">Loading semantic output…</div>;
  }
  if (status === "error") {
    return (
      <div className="kf-code__placeholder kf-code__placeholder--err" role="alert">
        Failed to load semantic output
        {errorMessage ? <>: <code>{errorMessage}</code></> : null}
      </div>
    );
  }
  if (status === "absent" || !semantic) {
    return (
      <div className="kf-code__placeholder">
        No semantic output yet — run Semantic on the version above.
      </div>
    );
  }
  if (tab === "preview") {
    return (
      <div className="kf-md orb-scroll" data-testid="kf-sem-preview">
        {markdown ? (
          <pre className="kf-md__pre">{markdown}</pre>
        ) : (
          <div className="kf-md__no-md orb-mono">
            No rendered Markdown — switch to <b>source</b>.
          </div>
        )}
        <div className="kf-md__fade" aria-hidden="true" />
      </div>
    );
  }
  if (tab === "source") {
    return (
      <pre className="kf-code orb-mono orb-scroll" data-testid="kf-sem-source">
        {JSON.stringify(semantic, null, 2)}
      </pre>
    );
  }
  // diff — placeholder
  return (
    <div className="kf-code__placeholder" data-testid="kf-sem-diff">
      Diff view ships in a follow-up; for now compare the source tab
      against the previous version.
    </div>
  );
}
