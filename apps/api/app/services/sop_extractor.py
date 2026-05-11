"""Deterministic SOP detector + parser (#390).

Recognises numbered-step structure in a :class:`SemanticDocument`
and emits a :class:`Process` (with ordered :class:`ProcessStep`
rows) when one is found. Pure functions, no I/O, no LLM — operators
who want LLM-grade SOP extraction can layer it later. This slice
ships the foundation that's reliable enough to flag real SOPs
without false positives on non-procedural docs.

Detection is deliberately **conservative**: false negatives are
fine; false positives are not. A document that triggers on
"1. Background\\n2. Scope" without procedural meat must not be
flagged. The thresholds (≥3 numbered items / step headings) are
tuned against the in-repo SOP fixtures: the policy/specification
docs that opened #390 score below threshold; the manufacturing
paint-shop SOP scores above.

Wire-up
-------

The parser is invoked as a fire-and-log side-effect of
:meth:`KnowledgeProjector.project` once the structural projection
completes. Failures are swallowed (``log.warning``) per ADR-012 §3
— the same posture the embedding write and the document_relations
cache warm already use. Re-projection is idempotent: the projector
calls :meth:`ProcessStore.delete_for_version` before
:meth:`ProcessStore.save_process` so a re-extraction replaces the
prior Process atomically.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.schemas.document import Document, DocumentVersion
from app.schemas.process import Process, ProcessStep
from app.schemas.semantic_document import SemanticDocument, SemanticSection

# Detection thresholds. Three is the smallest number that reliably
# distinguishes a procedural list from a TOC / outline (which
# typically has 2 items at the top: "Background" + "Scope"). Bump
# only after a fixture pass — the false-positive risk grows fast.
_MIN_NUMBERED_ITEMS = 3

# ``1.`` / ``2.`` / ``3.`` at line start, optionally indented.
# The capturing group on the integer lets the parser tell ordered
# sequences apart from "1. foo / 5. bar / 9. baz" (which we'd
# rather not flag as a procedural list).
_NUMBERED_LINE_RE = re.compile(r"^[ \t]*(\d+)\.[ \t]+\S", re.MULTILINE)

# ``SemanticSection.heading`` arrives with markdown markers stripped
# (semantic extraction normalises them away), so the heading-level
# check matches a bare label like ``Step 1``. Body text on the
# other hand DOES preserve markdown markers, which is the only
# distinguishing signal between a real procedural heading
# (``## Step 1``) and prose narration (``Step 1 was completed
# yesterday.``). We use two regexes:
#
# * ``_STEP_HEADING_RE`` — lenient; for ``section.heading`` lookups
#   only.
# * ``_STEP_HEADING_BODY_RE`` — strict; requires markdown ``#+``
#   markers, used when scanning aggregated body text.
_STEP_HEADING_RE = re.compile(r"^[ \t]*#*[ \t]*step\s+(\d+)\b", re.IGNORECASE | re.MULTILINE)
_STEP_HEADING_BODY_RE = re.compile(r"^[ \t]*#+[ \t]+step\s+(\d+)\b", re.IGNORECASE | re.MULTILINE)

# Inline ``Step 1:`` line. Matches at start of line because mid-
# paragraph "step 1:" callouts are usually not the procedural
# spine (e.g. "see step 1: above" inside a discussion section).
_STEP_LINE_RE = re.compile(r"^[ \t]*step\s+(\d+):", re.IGNORECASE | re.MULTILINE)

# ``# How to onboard`` / ``# How do I X`` — heading-only marker
# that says "this is a procedure" but doesn't itself have step
# numbers. Used as a tie-breaker: if at least one such heading is
# present we accept the doc as procedural even when the numbered
# count just misses the threshold. NOT used today (the threshold
# is conservative enough on its own); kept as a comment so future
# tuners know the next dial to consider.


def detect_sop_structure(semantic: SemanticDocument) -> bool:
    """Return ``True`` when the document looks procedural.

    Triggers on any of:

    * ≥3 sequentially-numbered list items (``1.`` / ``2.`` / ``3.``)
      contiguous at the start of lines, in ascending order, in a
      single section's text.
    * ≥3 markdown step headings (``## Step N``).
    * ≥3 explicit ``Step N:`` lines.

    Conservative by design — false negatives are fine; false
    positives are not. Returns ``False`` on empty / non-procedural
    documents.
    """
    if not semantic.sections:
        return False

    # Step-headings: count ``Step N`` markers in section headings
    # (``SemanticSection.heading`` is the raw label sans markdown
    # markers — semantic extraction strips them) AND in the bodies
    # for documents whose extractor preserves the markdown form.
    heading_hits = sum(
        1
        for section in semantic.sections
        if section.heading and _STEP_HEADING_RE.match(section.heading)
    )
    if heading_hits >= _MIN_NUMBERED_ITEMS:
        return True

    # Aggregate body counts across the document. Step-headings and
    # ``Step N:`` lines naturally aggregate (operators sometimes
    # split a playbook across sections); numbered-list items are
    # counted *per section* because a numbered list rarely spans
    # two sections in practice and aggregating them risks
    # combining a TOC's "1. Background\\n2. Scope" with a body
    # section's standalone "1. Item / 2. Item" into a false hit.
    full_text = "\n\n".join(section.text for section in semantic.sections)
    # Strict body regex: requires markdown ``#+`` markers so prose
    # narration ("Step 1 was completed yesterday. Step 2 had a
    # delay...") doesn't trip the detector. Real SOPs preserve
    # markdown headings in the body.
    if len(_STEP_HEADING_BODY_RE.findall(full_text)) >= _MIN_NUMBERED_ITEMS:
        return True
    if len(_STEP_LINE_RE.findall(full_text)) >= _MIN_NUMBERED_ITEMS:
        return True

    # Numbered-list items: per-section threshold AND ascending.
    return any(_has_numbered_run(section.text) for section in semantic.sections)


def extract_process(
    semantic: SemanticDocument,
    *,
    document: Document,
    version: DocumentVersion,
    now: datetime | None = None,
) -> Process | None:
    """Return a :class:`Process` for ``semantic`` when it looks
    procedural; ``None`` otherwise.

    Each emitted :class:`ProcessStep` carries the section id it was
    derived from in ``source_reference_ids`` so AURA citation
    surfaces (ADR-029) can trace back to the source chunk. Step
    ``title`` is the section heading or, when the heading is empty,
    the first sentence of the body. ``preconditions`` and
    ``outcomes`` default empty — those are populated by a future
    LLM-grade pass; this slice keeps the deterministic surface
    minimal.

    The Process ``id`` is deterministic
    (``f"process-{version.id}"``): one Process per version is the
    contract at this slice, which makes the re-projection path
    clean (``delete_for_version`` followed by a fresh
    ``save_process`` writes exactly one row).

    ``now`` is for tests; defaults to ``datetime.now(UTC)`` when
    unset. The store overrides ``created_at`` on save anyway, so
    the value here is just a sentinel for the schema.
    """
    if not detect_sop_structure(semantic):
        return None

    steps = _segment_into_steps(semantic.sections)
    if not steps:
        # The detector said "yes" but the segmenter couldn't split
        # the body — give up rather than emit a one-step Process,
        # which would just be a re-skin of the original chunk.
        return None

    when = now or datetime.now(UTC)
    title = semantic.document_profile.title or "Untitled procedure"
    return Process(
        id=f"process-{version.id}",
        title=title,
        document_id=document.id,
        version_id=version.id,
        steps=steps,
        created_at=when,
    )


# ─── Internal helpers ─────────────────────────────────────────────


def _has_numbered_run(text: str) -> bool:
    """Return ``True`` iff ``text`` carries ≥``_MIN_NUMBERED_ITEMS``
    sequentially-numbered items (``1.`` / ``2.`` / ``3.``) in order
    AND each item has a non-trivial body (i.e. not just a one-word
    TOC entry).

    The "non-trivial body" check is what stops a TOC like
    "1. Background\\n2. Scope" from triggering: TOC entries are
    typically a heading word with no following sentence; procedural
    items carry an action verb plus an object.
    """
    matches = list(_NUMBERED_LINE_RE.finditer(text))
    if len(matches) < _MIN_NUMBERED_ITEMS:
        return False

    # The numbers themselves must be ascending and start at 1 (or
    # close to it — operators occasionally start a sublist at 1
    # under a heading and we should still accept that). Out-of-order
    # numbers ("1. foo / 5. bar / 2. baz") suggest unrelated mentions
    # of the digit 1./2./5. rather than a procedural list.
    numbers = [int(m.group(1)) for m in matches]
    if numbers[0] != 1:
        return False
    for prev, curr in zip(numbers, numbers[1:], strict=False):
        if curr != prev + 1:
            return False

    # Body-content guard: every numbered item should carry an
    # action-shaped sentence, not a bare label. Slice the text
    # from each match's end to the next match's start (or end-of-
    # text for the last) and require ≥6 words on average. The
    # threshold was tuned to reject the policy-doc structure
    # "1. Background. The audit covers." (~4 words) while
    # accepting concise procedural items like "Acquire the lock
    # before mutation." (~6 words).
    #
    # The narrative-prose false positive ("Step 1 was X. Step 2
    # was Y.") is caught separately by the strict
    # ``_STEP_HEADING_BODY_RE`` (requires markdown ``#+``
    # markers); this guard exists for the orthogonal
    # numbered-list-TOC failure mode the reviewer flagged.
    bodies: list[str] = []
    for i, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        bodies.append(text[body_start:body_end].strip())

    avg_words = sum(len(body.split()) for body in bodies) / len(bodies)
    return avg_words >= 6.0


def _segment_into_steps(sections: list[SemanticSection]) -> list[ProcessStep]:
    """Walk the sections and emit one :class:`ProcessStep` per
    detected step.

    Strategy (in order — the first one that yields steps wins):

    1. **Heading-driven**: a ``## Step N`` heading defines a step;
       its section's body is the step body.
    2. **Inline ``Step N:``**: split a section's text at each
       ``Step N:`` boundary; each chunk becomes a step.
    3. **Numbered list**: split a section's text at each ``N.``
       boundary; each chunk becomes a step.

    The first strategy that produces ≥``_MIN_NUMBERED_ITEMS`` steps
    wins. Falling back is fine: a doc with both a numbered list and
    a stray ``Step 1:`` line in a different section uses the more
    structurally explicit signal.
    """
    # Strategy 1: heading-driven.
    heading_steps = _steps_from_step_headings(sections)
    if len(heading_steps) >= _MIN_NUMBERED_ITEMS:
        return heading_steps

    # Strategies 2 + 3: per-section, the first that yields enough
    # steps wins for that section. The aggregate is what we return.
    for section in sections:
        line_steps = _steps_from_step_lines(section)
        if len(line_steps) >= _MIN_NUMBERED_ITEMS:
            return line_steps
        numbered_steps = _steps_from_numbered_list(section)
        if len(numbered_steps) >= _MIN_NUMBERED_ITEMS:
            return numbered_steps

    return []


def _steps_from_step_headings(
    sections: list[SemanticSection],
) -> list[ProcessStep]:
    """Treat each ``## Step N`` section as one ordered step."""
    steps: list[ProcessStep] = []
    for section in sections:
        match = _STEP_HEADING_RE.match(section.heading or "")
        if match is None:
            continue
        step_number = int(match.group(1))
        title, body = _split_title_and_body(section.heading, section.text)
        steps.append(
            ProcessStep(
                step_number=step_number,
                title=title,
                body=body,
                source_reference_ids=[section.id],
            )
        )
    steps.sort(key=lambda s: s.step_number)
    return _renumber(steps)


def _steps_from_step_lines(section: SemanticSection) -> list[ProcessStep]:
    """Split ``section.text`` on each ``Step N:`` line."""
    matches = list(_STEP_LINE_RE.finditer(section.text))
    if len(matches) < _MIN_NUMBERED_ITEMS:
        return []
    steps: list[ProcessStep] = []
    for i, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(section.text)
        chunk = section.text[body_start:body_end].strip()
        title, body = _title_and_body_from_chunk(chunk)
        steps.append(
            ProcessStep(
                step_number=int(match.group(1)),
                title=title,
                body=body,
                source_reference_ids=[section.id],
            )
        )
    steps.sort(key=lambda s: s.step_number)
    return _renumber(steps)


def _steps_from_numbered_list(section: SemanticSection) -> list[ProcessStep]:
    """Split ``section.text`` on each ``N.`` boundary."""
    if not _has_numbered_run(section.text):
        return []
    matches = list(_NUMBERED_LINE_RE.finditer(section.text))
    steps: list[ProcessStep] = []
    for i, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(section.text)
        chunk = section.text[body_start:body_end].strip()
        title, body = _title_and_body_from_chunk(chunk)
        steps.append(
            ProcessStep(
                step_number=int(match.group(1)),
                title=title,
                body=body,
                source_reference_ids=[section.id],
            )
        )
    return _renumber(steps)


def _split_title_and_body(
    heading: str | None,
    text: str,
) -> tuple[str, str]:
    """Use the section heading as the step title; the section text
    as the body. Falls back to the first sentence of the body when
    no heading is set."""
    if heading:
        # Strip the leading "Step N" so the title is the operator-
        # written label rather than the step marker itself.
        cleaned = re.sub(
            r"^[ \t]*step\s+\d+\b[:\-—\s]*",
            "",
            heading,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        title = cleaned or heading.strip()
        body = text.strip()
        return _truncate_title(title), body
    return _title_and_body_from_chunk(text.strip())


def _title_and_body_from_chunk(chunk: str) -> tuple[str, str]:
    """Pull the first sentence as the title, leave the rest as body.

    A "sentence" here is the text up to the first period / newline
    (whichever comes first) — the wire contract caps the title at
    500 chars but operators usually write much shorter step labels.
    """
    if not chunk:
        return ("(empty step)", "")
    # First line is usually the step's one-line summary. Fall back
    # to the whole chunk if the first line is the entire chunk.
    first_break = min(
        (i for i in (chunk.find("."), chunk.find("\n")) if i != -1),
        default=-1,
    )
    if first_break == -1 or first_break == len(chunk) - 1:
        return (_truncate_title(chunk), chunk)
    title = chunk[:first_break].strip()
    body = chunk[first_break + 1 :].strip()
    return (_truncate_title(title or chunk), body or chunk)


def _truncate_title(title: str) -> str:
    """Cap to the schema limit (500 chars) without raising."""
    if len(title) <= 500:
        return title
    return title[:497].rstrip() + "…"


def _renumber(steps: list[ProcessStep]) -> list[ProcessStep]:
    """Return ``steps`` re-numbered ``1, 2, 3, …`` so the wire
    contract holds even when the source doc skipped a number
    (``1. / 2. / 4.``) or the heading-driven path gave us a
    sparse sequence.
    """
    return [step.model_copy(update={"step_number": i + 1}) for i, step in enumerate(steps)]


__all__ = [
    "detect_sop_structure",
    "extract_process",
]
