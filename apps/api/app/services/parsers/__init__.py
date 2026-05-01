"""Concrete parser implementations registered with :class:`ParserRegistry`.

This subpackage hosts content-type-specific parsers (DOCX, PDF today; OCR
later) so the originating ``document_parser`` module stays focused on the
``Parser`` Protocol and the registry mechanics.
"""

from app.services.parsers.docx import DocxParser
from app.services.parsers.pdf import PdfParser

__all__ = ["DocxParser", "PdfParser"]
