"""Concrete parser implementations registered with :class:`ParserRegistry`.

This subpackage hosts content-type-specific parsers (DOCX today; PDF/OCR
later) so the originating ``document_parser`` module stays focused on the
``Parser`` Protocol and the registry mechanics.
"""

from app.services.parsers.docx import DocxParser

__all__ = ["DocxParser"]
