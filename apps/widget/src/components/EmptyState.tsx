/**
 * Centered empty/placeholder treatment used by Recent documents and
 * other modes when they have nothing to show. Mirrors the
 * design-handoff `.hf-empty` block: dashed band, glyph chip, headline,
 * supporting copy, optional action row.
 */

import React from "react";

import { Icon, type IconName } from "./icons";

interface Props {
  icon: IconName;
  title: string;
  /** One-sentence supporting copy beneath the title. */
  body: string;
  /** Optional right-aligned action buttons (use `<button class="kw-btn">`). */
  actions?: React.ReactNode;
}

export const EmptyState: React.FC<Props> = ({ icon, title, body, actions }) => {
  return (
    <div className="kw-empty">
      <span className="kw-empty__glyph" aria-hidden="true">
        <Icon name={icon} size={18} />
      </span>
      <div className="kw-empty__title">{title}</div>
      <div className="kw-empty__body">{body}</div>
      {actions && <div className="kw-empty__actions">{actions}</div>}
    </div>
  );
};
