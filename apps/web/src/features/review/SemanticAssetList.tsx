/**
 * Reviewer-facing list of ``SemanticAsset`` rows (#408).
 *
 * Each row shows: ``type`` chip · text body · confidence bar ·
 * ``review_status`` pill · source-reference IDs (when present).
 *
 * Sort order: descending confidence, with a stable secondary sort on
 * ``id`` so two assets at the same confidence don't shuffle between
 * renders. Filter UI is intentionally NOT in this slice — the issue
 * (#408) calls out that per-asset triage is a separate workflow.
 * The reviewer's goal here is just to *read* what the extractor
 * produced before making the document-level validate/reject call.
 *
 * Long lists (default cap 12) get a ``+N more`` affordance.
 */

import type { ReactElement } from "react";
import type { ApiSemanticAsset, ReviewStatus } from "../../api/types";
import { TruncatedList } from "./TruncatedList";

export interface SemanticAssetListProps {
  assets: ApiSemanticAsset[];
  /** Optional cap before showing the +N more affordance. Default 12. */
  initialCount?: number;
}

const STATUS_LABELS: Record<ReviewStatus, string> = {
  needs_review: "needs review",
  source_backed: "source-backed",
  validated: "validated",
  rejected: "rejected",
};

function sortAssets(assets: ApiSemanticAsset[]): ApiSemanticAsset[] {
  // ``toSorted`` would be nicer but isn't widely supported across the
  // ES2020 lib target this app builds against. ``slice().sort()`` is
  // the portable equivalent.
  return assets.slice().sort((a, b) => {
    if (b.confidence !== a.confidence) return b.confidence - a.confidence;
    return a.id.localeCompare(b.id);
  });
}

export function SemanticAssetList({
  assets,
  initialCount = 12,
}: SemanticAssetListProps): ReactElement {
  if (assets.length === 0) {
    return (
      <p className="muted sem-empty" data-testid="sem-assets-empty">
        No assets extracted.
      </p>
    );
  }
  const sorted = sortAssets(assets);
  return (
    <ul className="sem-list sem-asset-list" data-testid="sem-assets-list">
      <TruncatedList
        items={sorted}
        initialCount={initialCount}
        testIdPrefix="sem-assets"
        renderItem={(asset) => <AssetRow key={asset.id} asset={asset} />}
      />
    </ul>
  );
}

function AssetRow({ asset }: { asset: ApiSemanticAsset }): ReactElement {
  const confidencePct = Math.round(asset.confidence * 100);
  return (
    <li className="sem-row sem-asset" data-testid="sem-asset-row">
      <div className="sem-asset__head">
        <span className="sem-asset__type" data-testid="sem-asset-type">
          {asset.type}
        </span>
        <span
          className={`sem-asset__status sem-asset__status--${asset.review_status}`}
          data-testid="sem-asset-status"
        >
          {STATUS_LABELS[asset.review_status]}
        </span>
      </div>
      <p className="sem-asset__text" data-testid="sem-asset-text">
        {asset.text}
      </p>
      <div className="sem-asset__confidence" title={`Confidence ${confidencePct}%`}>
        <div className="sem-asset__confidence-track" aria-hidden="true">
          <div
            className="sem-asset__confidence-fill"
            style={{ width: `${confidencePct}%` }}
          />
        </div>
        <span className="sem-asset__confidence-value" data-testid="sem-asset-confidence">
          {confidencePct}%
        </span>
      </div>
      {asset.source_reference_ids.length > 0 && (
        <p className="sem-asset__refs" data-testid="sem-asset-refs">
          <span className="sem-row__refs-label">Source refs:</span>{" "}
          {asset.source_reference_ids.map((ref, i) => (
            <span key={ref}>
              {i > 0 && " · "}
              <code>{ref}</code>
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
