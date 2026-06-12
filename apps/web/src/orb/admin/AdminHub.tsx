/**
 * AdminHub — tile grid landing page at /kf/admin per design §7.
 *
 * The Knowledge Forge admin surface is intentionally a thin re-skin of
 * the existing /admin routes (PR 8 deliberately does NOT reimplement
 * HITL / Audit / Archive — that work is shipped and stable, and a
 * second copy would diverge). Each tile links into the legacy admin
 * pages with breadcrumbs back to /kf.
 *
 * The "Config" tile opens the Knowledge Forge SettingsModal (PR 8) at
 * its Pipeline tab.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";

import { Card, CardHead, OrbI, SectionH } from "../index";
import "./admin.css";

interface TileDef {
  id: string;
  title: string;
  body: string;
  icon: ReactElement;
  href: string;
  external?: boolean;
}

// #441: ``external: false`` for the legacy admin tiles so React
// Router handles the click in-process. Previously we used
// ``<a href>`` for these, which triggered a full-page navigation; on
// the S3-hosted build that hits the bucket root and returns 403
// AccessDenied because ``/admin/hitl`` is not an object key. With
// ``<Link to>`` the SPA router intercepts the click, matches the
// outer ``/admin/*`` routes in App.tsx, and renders the legacy
// admin views without a server round-trip. The legacy views still
// carry their old Bulma styling — porting them into the Knowledge
// Forge shell is a separate follow-up.
const TILES: TileDef[] = [
  {
    id: "hitl",
    title: "HITL routing",
    body: "Bucket capacity bars, queue depth, drift alerts, and the auto-promotion trigger.",
    icon: OrbI.team,
    href: "/admin/hitl",
  },
  {
    id: "audit",
    title: "Audit log",
    body: "Filter-able timeline of every state transition with actor + payload + timestamp.",
    icon: OrbI.shield,
    href: "/admin/audit",
  },
  {
    id: "archive",
    title: "Archive",
    body: "Soft-deleted documents with restore + purge controls.",
    icon: OrbI.archive,
    href: "/admin/archive",
  },
  {
    id: "config",
    title: "Configuration",
    body: "Feature flags, env gates, per-pipeline overrides, and Phase-3 readiness.",
    icon: OrbI.cog,
    href: "/kf/settings",
  },
  {
    id: "roadmap",
    title: "Roadmap",
    body: "Vision gallery — every post-MVP feature on the converged plan, intentionally disabled so the demo doesn't over-promise.",
    icon: OrbI.spark,
    href: "/kf/admin/roadmap",
  },
];

export function AdminHub(): ReactElement {
  return (
    <section className="kf-admin" aria-label="Knowledge Forge — Admin">
      <header className="kf-admin__head">
        <h1 className="kf-admin__title">Admin</h1>
        <p className="kf-admin__subtitle">
          Operator surfaces — every link is gated on the admin role
          server-side; non-admins receive a friendly 403 panel rather
          than a raw error.
        </p>
      </header>

      <div className="kf-admin__tiles">
        {TILES.map((t) => (
          <AdminTile key={t.id} tile={t} />
        ))}
      </div>

      <Card className="kf-admin__activity">
        <CardHead
          right={<span className="orb-mono kf-card-hint">last 24 hours</span>}
        >
          <SectionH>Activity</SectionH>
        </CardHead>
        <div className="kf-admin__activity-body">
          <p>
            Activity sparkline ships in a follow-up — the admin summary
            endpoint exists and the chart wraps the same data the
            legacy hub renders.
          </p>
        </div>
      </Card>
    </section>
  );
}

function AdminTile({ tile }: { tile: TileDef }): ReactElement {
  const inner = (
    <>
      <div className="kf-admin__tile-h">
        <span className="kf-admin__tile-icon" aria-hidden="true">
          {tile.icon}
        </span>
        <h3 className="kf-admin__tile-title">{tile.title}</h3>
        <span className="orb-mono kf-admin__tile-arrow" aria-hidden="true">
          →
        </span>
      </div>
      <p className="kf-admin__tile-body">{tile.body}</p>
    </>
  );

  if (tile.external) {
    return (
      <a
        className="kf-admin__tile"
        href={tile.href}
        data-testid={`kf-admin-tile-${tile.id}`}
      >
        {inner}
      </a>
    );
  }
  return (
    <Link
      to={tile.href}
      className="kf-admin__tile"
      data-testid={`kf-admin-tile-${tile.id}`}
    >
      {inner}
    </Link>
  );
}
