/**
 * Section header used at the top of every active-mode body
 * (Backend health, Upload, Recent documents, Knowledge layer).
 *
 * Layout: icon + title on the left, optional meta string + caller-
 * supplied actions + always-on overflow menu on the right. Matches
 * the design-handoff `SectionHdr` shape.
 *
 * The overflow menu is a visual placeholder for now — clicking it
 * does nothing. It exists so the per-card actions surface (e.g.
 * "Open full review" on Documents, "Reset graph" on Knowledge) has
 * a designated home when those features land.
 */

import React from "react";

import { Icon, type IconName } from "./icons";

interface Props {
  icon: IconName;
  title: string;
  /** Right-aligned mono meta (e.g. "auto · 30s", "25 of 142"). */
  meta?: string;
  /** Optional inline action node — e.g. a small button. */
  actions?: React.ReactNode;
}

export const SectionHeader: React.FC<Props> = ({ icon, title, meta, actions }) => {
  return (
    <div className="kw-section__hdr">
      <div className="kw-section__title">
        <Icon name={icon} />
        <span>{title}</span>
      </div>
      <div className="kw-section__actions">
        {meta && <span className="kw-section__meta">{meta}</span>}
        {actions}
        <button
          type="button"
          className="kw-iconbtn"
          aria-label={`More options for ${title}`}
        >
          <Icon name="more" />
        </button>
      </div>
    </div>
  );
};
