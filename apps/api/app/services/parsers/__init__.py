"""Concrete parser implementations registered with :class:`ParserRegistry`.

This subpackage hosts content-type-specific parsers (DOCX, PDF, PPTX
today; OCR later) so the originating ``document_parser`` module stays
focused on the ``Parser`` Protocol and the registry mechanics.
"""

from app.services.parsers.docx import DocxParser
from app.services.parsers.pdf import PdfParser
from app.services.parsers.pptx import PptxParser

__all__ = ["DocxParser", "PdfParser", "PptxParser"]
