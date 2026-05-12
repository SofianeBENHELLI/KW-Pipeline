/**
 * OrbI — Knowledge Forge icon namespace.
 *
 * Thin wrapper around `lucide-react` so call sites stay terse:
 *   <span>{OrbI.search}</span>
 * matches the prototype's `OrbI.search` lookup pattern. Default sizing
 * (14px / 1.4 stroke) follows the design handoff §1.2: "13–14px, stroke
 * 1.4, no fills except play/pause/dot/grip".
 *
 * Why a frozen object instead of a `function getIcon(name)` lookup? It
 * keeps the surface tree-shake-friendly — only icons referenced in the
 * Knowledge Forge bundle ship; we cannot accidentally pull in the full
 * lucide-react set via dynamic indexing.
 */
import {
  AlertTriangle,
  Archive,
  Bolt,
  ChevronDown,
  ChevronRight,
  Check,
  ExternalLink,
  Filter,
  GripVertical,
  Link2,
  ListFilter,
  MessageSquare,
  Pause,
  Play,
  Plus,
  Power,
  RotateCw,
  Search,
  Settings,
  Shield,
  Sparkles,
  Trash2,
  User,
  Users,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import type { ReactElement } from "react";

const SZ = 14 as const;
const SW = 1.4 as const;

/** Single-source-of-truth icon set, mirroring the prototype's `I.*` map. */
export const OrbI = Object.freeze({
  search:  (<Search        size={SZ} strokeWidth={SW} />) as ReactElement,
  plus:    (<Plus          size={SZ} strokeWidth={SW} />) as ReactElement,
  check:   (<Check         size={SZ} strokeWidth={1.6} />) as ReactElement,
  x:       (<X             size={SZ} strokeWidth={SW} />) as ReactElement,
  chev:    (<ChevronRight  size={12} strokeWidth={SW} />) as ReactElement,
  chevD:   (<ChevronDown   size={10} strokeWidth={1.6} />) as ReactElement,
  doc:     (<Workflow      size={13} strokeWidth={SW} />) as ReactElement,
  filter:  (<Filter        size={13} strokeWidth={SW} />) as ReactElement,
  filterList: (<ListFilter size={13} strokeWidth={SW} />) as ReactElement,
  graph:   (<Workflow      size={13} strokeWidth={SW} />) as ReactElement,
  spark:   (<Sparkles      size={13} strokeWidth={SW} />) as ReactElement,
  chat:    (<MessageSquare size={13} strokeWidth={SW} />) as ReactElement,
  cog:     (<Settings      size={13} strokeWidth={SW} />) as ReactElement,
  alert:   (<AlertTriangle size={13} strokeWidth={1.5} />) as ReactElement,
  trash:   (<Trash2        size={13} strokeWidth={SW} />) as ReactElement,
  shield:  (<Shield        size={13} strokeWidth={SW} />) as ReactElement,
  bolt:    (<Bolt          size={13} strokeWidth={SW} />) as ReactElement,
  zap:     (<Zap           size={13} strokeWidth={SW} />) as ReactElement,
  archive: (<Archive       size={13} strokeWidth={SW} />) as ReactElement,
  user:    (<User          size={13} strokeWidth={SW} />) as ReactElement,
  team:    (<Users         size={13} strokeWidth={SW} />) as ReactElement,
  link:    (<Link2         size={13} strokeWidth={SW} />) as ReactElement,
  refresh: (<RotateCw      size={13} strokeWidth={SW} />) as ReactElement,
  ext:     (<ExternalLink  size={10} strokeWidth={SW} />) as ReactElement,
  play:    (<Play          size={11} strokeWidth={1} fill="currentColor" />) as ReactElement,
  pause:   (<Pause         size={11} strokeWidth={1} fill="currentColor" />) as ReactElement,
  grip:    (<GripVertical  size={10} strokeWidth={SW} />) as ReactElement,
  power:   (<Power         size={13} strokeWidth={SW} />) as ReactElement,
});

export type OrbIconName = keyof typeof OrbI;
