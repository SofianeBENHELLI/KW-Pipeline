/**
 * Admin UI — Navigation hub at ``/admin``.
 *
 * A landing page that lists every admin sub-tool as a card. Reachable
 * directly via ``/admin`` (no implicit redirect) for admin-role users
 * — non-admins still bounce off the per-route 403 once they click
 * through. The hub itself is reachable to everyone because the role
 * is not exposed to the frontend (ADR-019 §3); the cards just become
 * dead-ends for non-admin users.
 *
 * Closes the discoverability gap left by previous admin PRs: each
 * route was direct-only (#274 Archive, #278 HITL, parallel #279
 * Audit log), so a fresh admin had to know the URLs by heart. The
 * hub is the single jumping-off point.
 *
 * UX decisions worth flagging:
 *
 * - The whole card is clickable (``onClick`` on the wrapping
 *   ``button``). The chevron is a visual affordance, not a separate
 *   target — testing showed users habitually clicked anywhere on
 *   the card rather than aiming for the icon.
 * - The Audit-log card stays in the grid even if ``/admin/audit``
 *   isn't deployed yet. Clicking through will 404, which is acceptable
 *   for the small window where the parallel PR hasn't merged. The
 *   alternative (probe the route on mount, disable on 404) was
 *   evaluated and rejected as too clever for the slice.
 */

import { useNavigate } from "react-router-dom";

interface HubCard {
  /** Route to navigate to on click. */
  href: string;
  /** Card title — drives the visible heading. */
  title: string;
  /** Sub-line description rendered under the title. */
  description: string;
  /** ``data-testid`` for unit-test selection. */
  testId: string;
}

const HUB_CARDS: readonly HubCard[] = [
  {
    href: "/admin/archive",
    title: "Archive",
    description:
      "Manage archived documents — unarchive, relink scopes, purge artifacts.",
    testId: "admin-hub-card-archive",
  },
  {
    href: "/admin/hitl",
    title: "HITL",
    description:
      "Inspect HITL routing state, drift, and the auto-promotion queue.",
    testId: "admin-hub-card-hitl",
  },
  {
    href: "/admin/audit",
    title: "Audit log",
    description:
      "Filter the audit event store by event, actor, and time range.",
    testId: "admin-hub-card-audit",
  },
  {
    href: "/admin/reconcile",
    title: "Reconcile queue",
    description:
      "Drain the stuck-extraction queue — flip QUEUED / EXTRACTING rows to FAILED so operators can recover them.",
    testId: "admin-hub-card-reconcile",
  },
];

export function AdminHubView() {
  const navigate = useNavigate();

  return (
    <main className="app-shell admin-shell" aria-label="Admin navigation hub">
      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2>Administration</h2>
            <p className="muted">Tools for operators with the admin role.</p>
          </div>
        </header>

        <div className="admin-hub-grid" data-testid="admin-hub-grid">
          {HUB_CARDS.map((card) => (
            <button
              key={card.href}
              type="button"
              className="admin-hub-card"
              data-testid={card.testId}
              onClick={() => navigate(card.href)}
            >
              <div className="admin-hub-card-body">
                <h3>{card.title}</h3>
                <p className="muted">{card.description}</p>
              </div>
              {/* Chevron is decorative — the whole card is the click
                  target. ``aria-hidden`` keeps it out of the a11y tree
                  so screen readers announce only the heading + body. */}
              <span className="admin-hub-card-chevron" aria-hidden="true">
                ›
              </span>
            </button>
          ))}
        </div>

        <p className="muted admin-hub-footer">
          All admin actions require the admin role on your auth token.
          Routes return 403 if your role is insufficient.
        </p>
      </section>
    </main>
  );
}
