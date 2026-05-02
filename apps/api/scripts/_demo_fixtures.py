"""Helpers that materialise binary demo fixtures on first run.

Plain-text demo fixtures are committed under ``apps/api/fixtures/demo/``;
the PDF and DOCX cousins are *generated* the first time the seed script
runs because committing PDF/DOCX bytes is noisy in diffs and embeds
timestamps. This module owns those generators.

Both generators are deterministic in *content* — the same call always
produces a one-page PDF with the same text and a tiny DOCX with the same
paragraphs — but the byte stream is not bit-identical run-to-run because
``fpdf2`` and ``python-docx`` both stamp creation metadata. That is fine:
duplicate detection in the demo is exercised by the
``supplier_quality_policy_v1_renamed.txt`` pair, not by the binary
fixtures.

Imports of ``fpdf`` and ``docx`` are intentionally inside the functions
so this module can be imported under environments where those packages
are absent (the seed script's ``--help`` should not crash when fpdf2
isn't installed yet). Both packages ship in the API's ``[test]`` extras.
"""

from __future__ import annotations

from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "demo"
PDF_NAME = "change_request.pdf"
DOCX_NAME = "meeting_notes.docx"


def materialise_pdf(target: Path | None = None) -> Path:
    """Write a small one-page PDF to ``target`` (default: demo dir).

    Returns the path that was written. If the file already exists the
    function is a no-op — re-running the seed script must not churn
    fixture timestamps.
    """
    from fpdf import FPDF

    out = target if target is not None else DEMO_DIR / PDF_NAME
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(w=0, h=8, text="Change Request CR-2026-0142", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(w=0, h=8, text="Submitted: 2026-04-22", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", size=10)
    body = (
        "Replace fastener bracket P/N 4471-A with revised P/N 4471-B "
        "across line 3. Affects approx. 240 assemblies in WIP. "
        "Quality and operations have signed off. Effectivity: next "
        "production batch following approval."
    )
    pdf.multi_cell(w=0, h=5, text=body)
    pdf.output(str(out))
    return out


def materialise_docx(target: Path | None = None) -> Path:
    """Write a small DOCX with a few paragraphs to ``target``.

    Returns the path that was written. No-op when the file already
    exists, same rationale as :func:`materialise_pdf`.
    """
    from docx import Document

    out = target if target is not None else DEMO_DIR / DOCX_NAME
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading("Weekly Quality Review — 2026-04-15", level=1)
    doc.add_paragraph("Attendees: J. Pak (chair), M. Ortega, R. Devereaux, S. Cho.")
    doc.add_paragraph(
        "Open NCRs: 7 (down from 11 last week). Two Major findings "
        "remain past the 10-day containment window and have been "
        "escalated to the supplier business review."
    )
    doc.add_paragraph(
        "Action: tighten AQL on lot 4471-* shipments to 1.5 until the "
        "supplier closes the bracket-fit corrective action."
    )
    doc.save(str(out))
    return out


def materialise_all() -> list[Path]:
    """Generate every binary demo fixture that isn't already on disk."""
    return [materialise_pdf(), materialise_docx()]
