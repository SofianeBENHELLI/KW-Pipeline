/**
 * Inline SVG icon set for the widget.
 *
 * Lifted from the design handoff mockup (`hifi.js` `HFIcon`) so the
 * line weight, cap, and visual register match what the designer
 * approved. All icons are 24x24 viewBox, drawn with `currentColor`
 * so callers control colour via CSS, and use `strokeWidth=1.5` for
 * the DS-platform line-icon look.
 *
 * Add a new icon by appending a case to the switch — keep the
 * 24x24 viewBox and `strokeWidth=1.5` to stay visually consistent.
 */

import React from "react";

export type IconName =
  | "arrow-down"
  | "check"
  | "clock"
  | "cog"
  | "cross"
  | "docs"
  | "files"
  | "folder"
  | "graph"
  | "info"
  | "more"
  | "plus"
  | "pulse"
  | "refresh"
  | "search"
  | "upload"
  | "upload-cloud"
  | "warn";

interface IconProps {
  name: IconName;
  /** px size; defaults to 14 to match the mockup density. */
  size?: number;
  /** Optional aria-label; if omitted the icon is decorative (`aria-hidden`). */
  label?: string;
  /** Pass-through className for layout-level overrides. */
  className?: string;
}

export const Icon: React.FC<IconProps> = ({ name, size = 14, label, className }) => {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    role: label ? "img" : undefined,
    "aria-hidden": label ? undefined : true,
    "aria-label": label,
  };

  switch (name) {
    case "pulse":
      return (
        <svg {...common}>
          <path d="M3 12h3l3-7 4 14 3-7h5" />
        </svg>
      );
    case "upload":
      return (
        <svg {...common}>
          <path d="M12 4v12M6 10l6-6 6 6" />
          <path d="M4 20h16" />
        </svg>
      );
    case "upload-cloud":
      return (
        <svg {...common}>
          <path d="M16 16a5 5 0 1 0-9-3" />
          <path d="M7 13a4 4 0 0 0 0 8h11a4 4 0 0 0 1-7.9" />
          <path d="M12 12v8M9 15l3-3 3 3" />
        </svg>
      );
    case "docs":
      return (
        <svg {...common}>
          <path d="M6 3h9l4 4v14H6z" />
          <path d="M15 3v4h4" />
        </svg>
      );
    case "graph":
      return (
        <svg {...common}>
          <circle cx="6" cy="6" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="12" cy="18" r="2" />
          <circle cx="6" cy="14" r="1.4" />
          <circle cx="18" cy="14" r="1.4" />
          <path d="M7 7l4 9M17 7l-4 9M7.5 8l9 5.5M16.5 8l-9 5.5" />
        </svg>
      );
    case "cog":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19 12a7 7 0 0 1-.2 1.6l2 1.5-2 3.4-2.3-.9a7 7 0 0 1-2.7 1.6l-.4 2.5h-3.8l-.4-2.5a7 7 0 0 1-2.7-1.6l-2.3.9-2-3.4 2-1.5A7 7 0 0 1 5 12a7 7 0 0 1 .2-1.6l-2-1.5 2-3.4 2.3.9a7 7 0 0 1 2.7-1.6L10.6 2h3.8l.4 2.5a7 7 0 0 1 2.7 1.6l2.3-.9 2 3.4-2 1.5A7 7 0 0 1 19 12z" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="6" />
          <path d="m20 20-3.5-3.5" />
        </svg>
      );
    case "more":
      return (
        <svg {...common}>
          <circle cx="5" cy="12" r="1" />
          <circle cx="12" cy="12" r="1" />
          <circle cx="19" cy="12" r="1" />
        </svg>
      );
    case "plus":
      return (
        <svg {...common}>
          <path d="M12 5v14M5 12h14" />
        </svg>
      );
    case "folder":
      return (
        <svg {...common}>
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        </svg>
      );
    case "files":
      return (
        <svg {...common}>
          <rect x="7" y="3" width="11" height="14" rx="1" />
          <path d="M5 7v12a2 2 0 0 0 2 2h10" />
        </svg>
      );
    case "check":
      return (
        <svg {...common}>
          <path d="m4 12 5 5L20 6" />
        </svg>
      );
    case "cross":
      return (
        <svg {...common}>
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      );
    case "warn":
      return (
        <svg {...common}>
          <path d="M12 3 2 20h20zM12 10v4M12 17v0" />
        </svg>
      );
    case "clock":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
      );
    case "info":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5M12 8v0" />
        </svg>
      );
    case "refresh":
      return (
        <svg {...common}>
          <path d="M4 12a8 8 0 0 1 14-5l2-2v6h-6l2-2a6 6 0 1 0 1 7" />
        </svg>
      );
    case "arrow-down":
      return (
        <svg {...common}>
          <path d="M12 5v14M6 13l6 6 6-6" />
        </svg>
      );
    default:
      return null;
  }
};
