# Orbital Widget UX Direction

## Purpose

Orbital is the reviewer-facing surface for the KW Pipeline document
intelligence MVP. It is not intended to be a fancy standalone product. The
interface should be an effective operational workbench that can later fit into
a 3DEXPERIENCE dashboard as a widget.

The MVP should therefore favor compact, traceable workflows over marketing
layout, decorative visuals, or large-screen-only experiences.

## Product Posture

- Operational SaaS-style console, not a public landing page.
- Dense enough for repeated use by reviewers and administrators.
- Clear lifecycle visibility for document versions.
- Fast access to review decisions, failure reasons, and generated artifacts.
- Built so the same components can render in compact widget mode and expanded
  workspace mode.

## 3DEXPERIENCE Widget Compatibility

The long-term target is to embed Orbital as a 3DEXPERIENCE-compatible widget.
The frontend should be prepared for that from the start:

- Treat the initial viewport as a dashboard tile, not a full application shell.
- Avoid global page assumptions such as full-window navigation, oversized
  headers, or app-wide decorative backgrounds.
- Keep layouts responsive to narrow and resizable containers.
- Keep interaction flows usable without opening many browser tabs.
- Use web-standard React components that can be hosted either standalone or
  inside a platform container.
- Avoid hardcoded brand styling that would conflict with future official
  Dassault Systemes / 3DEXPERIENCE design tokens.

Until official project-specific brand tokens are available, Orbital should use
a small local theme abstraction for:

- surface colors
- borders
- text and muted text
- action, success, warning, danger status colors
- spacing
- radius
- font family

That theme layer should be easy to replace once the official 3DEXPERIENCE
branding constraints are confirmed.

## Recommended MVP Shape

Start with two related modes.

### Widget Mode

Compact dashboard view for everyday monitoring and triage.

Primary content:

- recent documents
- pending review count
- failed extraction count
- duplicate count
- upload action
- quick status chips
- direct link to the selected document detail

Widget mode must remain useful in a small dashboard tile. It should not depend
on a wide table being visible.

### Expanded Mode

Full review workspace for document inspection.

Primary content:

- document and version header
- lifecycle status and metadata
- raw extraction panel
- semantic JSON panel
- Markdown preview panel
- source lineage visibility
- validate / reject actions
- reviewer note field
- failure reason display when relevant
- version history

Expanded mode may use split panes, tabs, or a detail drawer depending on final
space constraints. The first implementation should prefer simple, predictable
layout over visual novelty.

## Candidate Screens

### Compact Pipeline Widget

Best MVP entry point.

- Summary counters at the top.
- Recent document list below.
- Each row shows filename, version, lifecycle status, and last activity.
- Failed and review-needed rows are visually prominent but not loud.

### Review Queue Widget

Best daily-user workflow.

- Lists only `NEEDS_REVIEW` versions.
- Shows filename, version, generated timestamp, and short semantic summary.
- Provides validate / reject entry points.
- Opens document detail for source inspection before final decision.

### Document Detail Workspace

Best quality-control workflow.

- Header shows filename, version, hash, duplicate status, and lifecycle state.
- Raw extraction, semantic JSON, and Markdown are inspectable side by side or
  through tabs.
- Review actions stay visible while inspecting generated output.

### Pipeline Health Widget

Useful later, after ingestion volume exists.

- Uploaded today.
- Extracted today.
- Pending review.
- Failed jobs.
- Duplicate rate.
- Recent failure reasons.

This should not be the first MVP screen because it monitors activity rather
than completing the core review task.

## Component Direction

Build reusable components so widget mode and expanded mode share behavior:

- `PipelineWidget`
- `ReviewQueue`
- `DocumentList`
- `DocumentStatusBadge`
- `DocumentDetail`
- `VersionTimeline`
- `ExtractionPanel`
- `SemanticPanel`
- `MarkdownPanel`
- `ReviewActions`
- `FailureReason`
- `UploadControl`

Components should accept data from API-facing hooks or services rather than
calling `fetch` directly from deeply nested UI.

## Visual Tone

- Quiet, professional, and compact.
- Light neutral base with restrained status colors.
- Status chips for lifecycle states.
- Icons for repeated actions where available.
- Avoid hero sections, decorative cards, large gradients, and marketing copy.
- Avoid a one-color theme; the interface should read as a work tool, not a
  branded splash screen.

## Open Questions

- Which official 3DEXPERIENCE / Dassault Systemes design tokens are available
  to this project?
- Will the first deployment be standalone web, embedded widget, or both?
- What container sizes should the widget support in the target dashboard?
- Does widget embedding require platform APIs for authentication, context, or
  document selection?
- Should uploaded files come from local user input only, or eventually from
  3DEXPERIENCE platform objects?
