/**
 * Line-art icon set for the Knowledge Explorer (port of the design's
 * icons.jsx). All icons are 24x24 viewBox, drawn with `currentColor`,
 * stroke-only, 1.5 width — same visual register as the navy-blue
 * design language.
 *
 * Add a new icon by appending a case to the switch — keep the 24x24
 * viewBox and stroke-only style to stay consistent.
 */

import React from "react";

export type IconName =
  | "search"
  | "graph"
  | "doc"
  | "chunk"
  | "concept"
  | "filter"
  | "depth"
  | "expand"
  | "collapse"
  | "focus"
  | "x"
  | "chevron-right"
  | "chevron-left"
  | "home"
  | "chevron-down"
  | "chevron-up"
  | "external"
  | "highlight"
  | "info"
  | "stack"
  | "tag"
  | "clusters"
  | "share"
  | "settings"
  | "compass"
  | "layers"
  | "menu"
  | "arrow-right"
  | "play"
  | "puzzle"
  | "globe"
  | "shield"
  | "people"
  | "wallet"
  | "rocket"
  | "scale"
  | "warn"
  | "check"
  | "minus"
  | "plus"
  | "reset"
  | "page";

interface IconProps {
  name: IconName;
  /** px size; defaults to 16 to match the design density. */
  size?: number;
  /** Stroke colour; defaults to `currentColor`. */
  stroke?: string;
  className?: string;
  style?: React.CSSProperties;
}

export const Icon: React.FC<IconProps> = ({
  name,
  size = 16,
  stroke = "currentColor",
  className,
  style,
}) => {
  const props = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    stroke,
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    style,
    "aria-hidden": true as const,
  };
  switch (name) {
    case "search":
      return (
        <svg {...props}>
          <circle cx="11" cy="11" r="6" />
          <path d="M20 20l-4-4" />
        </svg>
      );
    case "graph":
      return (
        <svg {...props}>
          <circle cx="6" cy="7" r="2.2" />
          <circle cx="18" cy="9" r="2.2" />
          <circle cx="11" cy="17" r="2.2" />
          <path d="M8 8l8 1M8 8l3 7M16 10l-5 6" />
        </svg>
      );
    case "doc":
      return (
        <svg {...props}>
          <path d="M7 3h7l4 4v14H7z" />
          <path d="M14 3v4h4" />
          <path d="M9 12h7M9 15h7M9 18h4" />
        </svg>
      );
    case "chunk":
      return (
        <svg {...props}>
          <rect x="4" y="5" width="6" height="6" rx="1" />
          <rect x="14" y="5" width="6" height="6" rx="1" />
          <rect x="4" y="13" width="6" height="6" rx="1" />
          <rect x="14" y="13" width="6" height="6" rx="1" />
        </svg>
      );
    case "concept":
      return (
        <svg {...props}>
          <polygon points="12,3 20,7.5 20,16.5 12,21 4,16.5 4,7.5" />
        </svg>
      );
    case "filter":
      return (
        <svg {...props}>
          <path d="M3 5h18l-7 8v6l-4 2v-8z" />
        </svg>
      );
    case "depth":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="2.5" />
          <circle cx="12" cy="12" r="6" />
          <circle cx="12" cy="12" r="9.5" />
        </svg>
      );
    case "expand":
      return (
        <svg {...props}>
          <path d="M4 4h6M4 4v6M20 4h-6M20 4v6M4 20h6M4 20v-6M20 20h-6M20 20v-6" />
        </svg>
      );
    case "collapse":
      return (
        <svg {...props}>
          <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
        </svg>
      );
    case "focus":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
        </svg>
      );
    case "x":
      return (
        <svg {...props}>
          <path d="M5 5l14 14M19 5L5 19" />
        </svg>
      );
    case "chevron-right":
      return (
        <svg {...props}>
          <path d="M9 6l6 6-6 6" />
        </svg>
      );
    case "chevron-left":
      return (
        <svg {...props}>
          <path d="M15 6l-6 6 6 6" />
        </svg>
      );
    case "home":
      return (
        <svg {...props}>
          <path d="M3 11l9-8 9 8M5 9v11h14V9" />
        </svg>
      );
    case "chevron-down":
      return (
        <svg {...props}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      );
    case "chevron-up":
      return (
        <svg {...props}>
          <path d="M6 15l6-6 6 6" />
        </svg>
      );
    case "external":
      return (
        <svg {...props}>
          <path d="M14 4h6v6M20 4l-9 9M19 13v6H5V5h6" />
        </svg>
      );
    case "highlight":
      return (
        <svg {...props}>
          <path d="M5 16l4 4 10-10-4-4z" />
          <path d="M14 6l4 4M5 20h6" />
        </svg>
      );
    case "info":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v6M12 7.5v.5" />
        </svg>
      );
    case "stack":
      return (
        <svg {...props}>
          <path d="M12 3l9 5-9 5-9-5z" />
          <path d="M3 13l9 5 9-5M3 17l9 5 9-5" />
        </svg>
      );
    case "tag":
      return (
        <svg {...props}>
          <path d="M12 3H4v8l9 9 8-8z" />
          <circle cx="8" cy="7" r="1.2" />
        </svg>
      );
    case "clusters":
      return (
        <svg {...props}>
          <circle cx="7" cy="8" r="3" />
          <circle cx="17" cy="8" r="3" />
          <circle cx="12" cy="17" r="3" />
        </svg>
      );
    case "share":
      return (
        <svg {...props}>
          <circle cx="6" cy="12" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="18" cy="18" r="2" />
          <path d="M8 11l8-4M8 13l8 4" />
        </svg>
      );
    case "settings":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      );
    case "compass":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="9" />
          <path d="M15.5 8.5l-2 5-5 2 2-5z" />
        </svg>
      );
    case "layers":
      return (
        <svg {...props}>
          <path d="M12 3l9 5-9 5-9-5z" />
          <path d="M3 13l9 5 9-5" />
        </svg>
      );
    case "menu":
      return (
        <svg {...props}>
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      );
    case "arrow-right":
      return (
        <svg {...props}>
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
      );
    case "play":
      return (
        <svg {...props}>
          <polygon points="6,4 20,12 6,20" />
        </svg>
      );
    case "puzzle":
      return (
        <svg {...props}>
          <path d="M10 4h4v3a1.5 1.5 0 0 0 3 0V4h3v4h-2.5a1.5 1.5 0 0 0 0 3H20v4h-3v-2.5a1.5 1.5 0 0 0-3 0V14h-4v-2.5a1.5 1.5 0 0 1-3 0V14H4v-4h2.5a1.5 1.5 0 0 0 0-3H4V4h3v2.5a1.5 1.5 0 0 0 3 0z" />
        </svg>
      );
    case "globe":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
        </svg>
      );
    case "shield":
      return (
        <svg {...props}>
          <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
        </svg>
      );
    case "people":
      return (
        <svg {...props}>
          <circle cx="9" cy="8" r="3" />
          <circle cx="17" cy="9" r="2.5" />
          <path d="M3 20c0-3.5 2.7-5.5 6-5.5s6 2 6 5.5M14.5 20c0-2.5 1.7-4.2 4.5-4.2" />
        </svg>
      );
    case "wallet":
      return (
        <svg {...props}>
          <rect x="3" y="6" width="18" height="13" rx="2" />
          <path d="M3 10h18" />
          <circle cx="16" cy="14" r="1" />
        </svg>
      );
    case "rocket":
      return (
        <svg {...props}>
          <path d="M12 2c4 3 6 7 6 12l-3 2-3 2-3-2-3-2c0-5 2-9 6-12z" />
          <circle cx="12" cy="10" r="1.5" />
          <path d="M9 18l-3 4M15 18l3 4" />
        </svg>
      );
    case "scale":
      return (
        <svg {...props}>
          <path d="M12 3v18M5 6h14M8 6l-3 6h6zM19 6l-3 6h6z" />
        </svg>
      );
    case "warn":
      return (
        <svg {...props}>
          <path d="M12 3l10 18H2z" />
          <path d="M12 10v5M12 18v.5" />
        </svg>
      );
    case "check":
      return (
        <svg {...props}>
          <path d="M5 12l4 4 10-10" />
        </svg>
      );
    case "minus":
      return (
        <svg {...props}>
          <path d="M5 12h14" />
        </svg>
      );
    case "plus":
      return (
        <svg {...props}>
          <path d="M12 5v14M5 12h14" />
        </svg>
      );
    case "reset":
      return (
        <svg {...props}>
          <path d="M3 12a9 9 0 1 0 3-6.7" />
          <path d="M3 4v5h5" />
        </svg>
      );
    case "page":
      return (
        <svg {...props}>
          <rect x="5" y="3" width="14" height="18" rx="1" />
          <path d="M8 8h8M8 12h8M8 16h5" />
        </svg>
      );
    default:
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="6" />
        </svg>
      );
  }
};

// Design palette tokens used in inline SVG strokes outside the
// CSS-token system. Kept here so consumers can colour design-spec
// strokes without re-deriving the navy.
export const NAVY = "#0E2A4A";
export const NAVY2 = "#1B3E6F";
export const ACCENT = "#2D5BA8";
