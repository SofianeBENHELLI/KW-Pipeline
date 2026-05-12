/**
 * ReviewTab — orchestrates the Review tab cards (FSM + detail +
 * versions + extraction + semantic) per design §3.5.
 *
 * Grid layout:
 *
 *   ┌────────────────────────────────────┐
 *   │ Lifecycle (FSM)            full    │
 *   ├──────────────────┬─────────────────┤
 *   │ Document detail  │ Versions        │
 *   ├──────────────────┴─────────────────┤
 *   │ Raw extraction (json/spans/tables) │
 *   │ Semantic markdown (preview/source) │
 *   └────────────────────────────────────┘
 */

import type { ReactElement } from "react";

import { Card, CardHead, SectionH } from "../index";
import { DocumentDetailCard } from "./DocumentDetailCard";
import { FsmActions } from "./FsmActions";
import { RawExtractionTabs } from "./RawExtractionTabs";
import { SemanticMarkdownCard } from "./SemanticMarkdownCard";
import { VersionList } from "./VersionList";
import type { ApiDocument } from "../../api/types";
import { useExtraction } from "../hooks/useExtraction";
import {
  useFsmTransition,
  type FsmAction,
} from "../hooks/useFsmTransition";
import { useSemantic } from "../hooks/useSemantic";
import { latestStatus, latestVersion } from "./format";

export interface ReviewTabProps {
  document: ApiDocument | null;
  onAfterTransition?: (action: FsmAction) => void;
}

export function ReviewTab({
  document,
  onAfterTransition,
}: ReviewTabProps): ReactElement {
  const ver = latestVersion(document);
  const docId = document?.id ?? null;
  const verId = ver?.id ?? null;

  const fsm = useFsmTransition({
    documentId: docId,
    versionId: verId,
    currentStatus: latestStatus(document),
    onAfter: onAfterTransition,
  });
  const extraction = useExtraction(docId, verId);
  const semantic = useSemantic(docId, verId);

  if (!document) {
    return (
      <Card>
        <CardHead>
          <SectionH>Review</SectionH>
        </CardHead>
        <div className="kf-review-tab__empty">
          Pick a document from the rail to review.
        </div>
      </Card>
    );
  }

  const previousVersion =
    document.versions.length > 1
      ? document.versions[document.versions.length - 2]?.version_number ?? null
      : null;

  return (
    <section className="kf-review-tab" aria-label="Review tab">
      <Card className="kf-review-tab__fsmcard">
        <CardHead
          right={
            <span className="orb-mono kf-card-hint">
              STORED → EXTRACTED → SEMANTIC_READY → VALIDATED
            </span>
          }
        >
          <SectionH>Lifecycle</SectionH>
        </CardHead>
        <FsmActions
          gates={fsm.gates}
          status={fsm.status}
          activeAction={fsm.activeAction}
          error={fsm.error}
          onRun={fsm.run}
        />
      </Card>

      <DocumentDetailCard document={document} />
      <VersionList document={document} />

      <RawExtractionTabs
        status={extraction.status}
        extraction={extraction.extraction}
        errorMessage={extraction.error?.message ?? null}
      />
      <SemanticMarkdownCard
        status={semantic.status}
        semantic={semantic.semantic}
        markdown={semantic.markdown}
        errorMessage={semantic.error?.message ?? null}
        previousVersion={previousVersion}
      />
    </section>
  );
}
