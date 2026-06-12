/**
 * RoadmapView — vision gallery at /kf/admin/roadmap (converged
 * plan §C.3). Every card is intentionally disabled: it surfaces a
 * planned post-MVP feature so a demo audience can see where the
 * pipeline is going without believing those features ship today.
 *
 * The config that drives the gallery lives next door in
 * ``RoadmapView.config.ts`` — add or remove items there as the
 * roadmap moves. The view itself is a pure rendering surface.
 */

import type { ReactElement } from "react";

import { Card, CardHead, SectionH } from "../index";
import "./roadmap.css";
import {
  ROADMAP_CARDS,
  ROADMAP_CATEGORY_LABEL,
  ROADMAP_CATEGORY_ORDER,
  ROADMAP_EFFORT_LABEL,
  type RoadmapCard,
  type RoadmapCategory,
} from "./RoadmapView.config";

export function RoadmapView(): ReactElement {
  return (
    <section className="kf-roadmap" aria-label="Knowledge Forge — Roadmap">
      <header className="kf-roadmap__head">
        <h1 className="kf-roadmap__title">Roadmap</h1>
        <p className="kf-roadmap__subtitle">
          The features below are <strong>not yet shipped</strong>. Each
          card is disabled by design so the demo audience can tell apart
          the working pipeline from the post-MVP vision. Section
          references point into the{" "}
          <code>2026-05-17-converged-knowledge-pipeline-plan.md</code>{" "}
          roadmap document.
        </p>
      </header>

      {ROADMAP_CATEGORY_ORDER.map((category) => (
        <RoadmapSection key={category} category={category} />
      ))}
    </section>
  );
}

interface RoadmapSectionProps {
  category: RoadmapCategory;
}

function RoadmapSection({ category }: RoadmapSectionProps): ReactElement | null {
  const items = ROADMAP_CARDS.filter((c) => c.category === category);
  if (items.length === 0) return null;
  const meta = ROADMAP_CATEGORY_LABEL[category];
  return (
    <Card className="kf-roadmap__section">
      <CardHead
        right={
          <span className="orb-mono kf-card-hint">{items.length} planned</span>
        }
      >
        <SectionH>{meta.title}</SectionH>
      </CardHead>
      <p className="kf-roadmap__section-sub">{meta.description}</p>
      <ul
        className="kf-roadmap__grid"
        data-testid={`kf-roadmap-grid-${category}`}
      >
        {items.map((card) => (
          <li key={card.id}>
            <RoadmapCardTile card={card} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

interface RoadmapCardTileProps {
  card: RoadmapCard;
}

function RoadmapCardTile({ card }: RoadmapCardTileProps): ReactElement {
  const tooltip = card.blockedOn
    ? `Blocked on: ${card.blockedOn}`
    : `Planned — ${card.planSection}`;
  return (
    <button
      type="button"
      className="kf-roadmap__card"
      disabled
      aria-disabled="true"
      title={tooltip}
      data-testid={`kf-roadmap-card-${card.id}`}
    >
      <div className="kf-roadmap__card-h">
        <span className="kf-roadmap__card-title">{card.title}</span>
        <span
          className="kf-roadmap__chip orb-mono"
          data-testid={`kf-roadmap-effort-${card.id}`}
        >
          {ROADMAP_EFFORT_LABEL[card.effort]}
        </span>
      </div>
      <p className="kf-roadmap__card-body">{card.description}</p>
      <div className="kf-roadmap__card-foot orb-mono">
        <span>{card.planSection}</span>
        {card.tracking ? (
          <span className="kf-roadmap__card-tag">{card.tracking}</span>
        ) : null}
        {card.blockedOn ? (
          <span className="kf-roadmap__card-tag is-blocked">
            blocked · {card.blockedOn}
          </span>
        ) : null}
      </div>
    </button>
  );
}
