/**
 * TaxonomyModeBadge — cosmetic "active mode" pill at the top of the
 * left icon rail (ADR-018 §PR #346).
 *
 * Renders the currently-active taxonomy version's state pill plus a
 * compact ``vN`` prefix. The full ``version_label`` lives on the
 * button's ``title`` so a hover reveals the human-friendly name
 * without crowding the 48px-wide rail. Clicking the badge jumps to
 * the admin lineage view for the same taxonomy id.
 *
 * Failure posture is "render nothing":
 *
 *   - 503 (no active taxonomy yet) — the rail looks clean instead
 *     of carrying a noisy "unavailable" pill.
 *   - 403 (caller isn't admin) — the badge is reachable from every
 *     surface; the click destination handles auth, so a quiet
 *     no-render keeps the rail symmetric for non-admins.
 *   - Network errors / aborts — same posture; the badge is purely
 *     informational.
 *
 * The fetch fires once on mount; no polling, no auto-refresh. This
 * is operator context, not a live monitor.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { getActiveTaxonomy } from "../../api/client";
import type { ApiTaxonomy } from "../../api/types";
import { StatePill } from "../../features/admin/AdminTaxonomyView";

export function TaxonomyModeBadge() {
  const navigate = useNavigate();
  const [taxonomy, setTaxonomy] = useState<ApiTaxonomy | null>(null);

  useEffect(() => {
    // Abort on unmount so a slow response doesn't leak into a
    // stale-component setState warning.
    const controller = new AbortController();
    let cancelled = false;
    void getActiveTaxonomy({ signal: controller.signal })
      .then((next) => {
        if (cancelled) return;
        // Guard against unrelated 200 bodies (e.g. test stubs that
        // share one fetch mock for the whole shell) — the badge only
        // lights up when the lifecycle fields are actually present.
        if (
          next &&
          typeof next.taxonomy_id === "string" &&
          typeof next.version_number === "number" &&
          typeof next.state === "string"
        ) {
          setTaxonomy(next);
        }
      })
      .catch(() => {
        // Cosmetic surface — swallow every failure mode (503 / 403 /
        // network) and let the rail render without the pill.
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  if (!taxonomy) return null;

  // The tooltip prefers the operator-set label; fall back to the
  // version-number stub so a hover still surfaces something useful
  // when the label is null (which is the DRAFT default).
  const tooltip =
    taxonomy.version_label && taxonomy.version_label.length > 0
      ? taxonomy.version_label
      : `Taxonomy v${taxonomy.version_number}`;

  return (
    <button
      type="button"
      className="dx-taxonomy-badge"
      data-testid="taxonomy-mode-badge"
      title={tooltip}
      aria-label={`Active taxonomy: ${tooltip}`}
      onClick={() =>
        navigate(
          `/admin/taxonomy?taxonomy_id=${encodeURIComponent(taxonomy.taxonomy_id)}`,
        )
      }
    >
      <span className="dx-taxonomy-badge-version">
        v{taxonomy.version_number}
      </span>
      <StatePill state={taxonomy.state} />
    </button>
  );
}
