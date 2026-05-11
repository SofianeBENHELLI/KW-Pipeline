import { Link } from "react-router-dom";

import { Card, Icon, SectionHeading } from "../ui/orb";

import { OrbShell } from "./Shell";

const CARDS = [
  {
    href: "/orb/admin/audit",
    icon: "shield",
    title: "Audit log",
    body: "Every FSM transition, validate / reject, purge, config change.",
    internal: true,
  },
  {
    href: "/admin/archive",
    icon: "archive",
    title: "Archive",
    body: "Soft-removed documents. Unarchive, relink scope, or purge artefacts.",
    internal: false,
  },
  {
    href: "/admin/hitl",
    icon: "bolt",
    title: "HITL routing",
    body: "Auto-validation state, drift ratios, manual auto-promote pass.",
    internal: false,
  },
] as const;

/**
 * Phase-6 admin hub — the `/orb/admin` route. Four cards. The Audit
 * card opens the new orb-native viewer; the Archive + HITL cards link
 * to the legacy /admin/* surfaces that already exist (their full orb
 * redesign ships in a Phase-6 follow-up). External links are marked
 * with the `ext` icon so reviewers know they're crossing the boundary.
 */
export function OrbAdminHub() {
  return (
    <OrbShell rail={<HubRail />}>
      <div className="orb-admin">
        <h1 className="orb-admin__title">Admin</h1>
        <p className="orb-admin__subtitle">Operator tools. Backend gates every route with a 403 on non-admin.</p>
        <div className="orb-admin__cards">
          {CARDS.map((card) => (
            <Card key={card.href} className="orb-admin__card">
              <div className="orb-admin__card-icon">
                <Icon name={card.icon} size={18} />
              </div>
              <div className="orb-admin__card-body">
                <h2>{card.title}</h2>
                <p>{card.body}</p>
                {card.internal ? (
                  <Link to={card.href} className="orb-admin__card-cta">
                    Open →
                  </Link>
                ) : (
                  <a href={card.href} className="orb-admin__card-cta">
                    Open in legacy UI <Icon name="ext" />
                  </a>
                )}
              </div>
            </Card>
          ))}
        </div>
      </div>
    </OrbShell>
  );
}

function HubRail() {
  return (
    <div className="orb-rail">
      <div className="orb-rail__head">
        <SectionHeading>Admin</SectionHeading>
      </div>
      <nav className="orb-rail__views" aria-label="Admin navigation">
        <Link to="/orb" className="orb-rail__view">
          ← Back to catalog
        </Link>
      </nav>
    </div>
  );
}
