"""
Parse service.

Document Text Extraction Checkpoint 1 scope: a standalone PDF
text-extraction primitive, completely independent of FastAPI, HTTP,
SQLAlchemy, the Document model, and document_service — mirrors
storage_service.py's decoupling philosophy exactly (see that module's
docstring), so this is reusable by synchronous upload processing, a
future background worker, re-processing, CLI utilities, or tests,
without dragging any of those concerns in.

PDF only for this checkpoint. `.docx` and `.txt` are valid upload
extensions (see app.core.config.settings.allowed_upload_extensions)
but are explicitly unsupported *here* — see UnsupportedFormatError.
Extraction for those formats is a future checkpoint's decision, not
assumed by this one.

Extracts RAW TEXT only: no chunking, no cleaning for embeddings, no
tokenization, no OCR, no summarization. Whatever PyMuPDF reads off
each page is what comes back — the only normalization applied is
stripping each page's leading/trailing whitespace before joining
pages together, documented explicitly below.

Not wired into upload, document_service, or any endpoint in this
checkpoint — see PROJECT_CONTEXT.md for why that's a deliberately
separate, later decision.
"""
from pathlib import Path

import pymupdf

_SUPPORTED_EXTENSION = ".pdf"


class UnsupportedFormatError(Exception):
    """
    Raised when the file's extension isn't supported by this parser.
    PDF only, for this checkpoint — `.docx`/`.txt` (both valid upload
    extensions elsewhere in the app) are explicitly rejected here
    rather than silently mishandled.
    """


class ParseError(Exception):
    """
    Raised when a `.pdf`-extensioned file cannot actually be parsed —
    it doesn't exist, is empty, or is corrupted/not really a PDF.
    Wraps PyMuPDF's own exceptions (and a missing-file check) so
    callers deal with one domain exception rather than needing to
    know PyMuPDF's exception hierarchy. Deliberately does not include
    the filesystem path in its message, so this exception is safe to
    surface without leaking server-side directory structure.

    NOT raised for a valid PDF that simply contains no extractable
    text (e.g. a scanned image with no text layer) — that's a
    legitimate, different outcome; see extract_text()'s docstring.
    """


def extract_text(file_path: Path) -> str:
    """
    Extract raw text from a PDF file on disk, page by page, in order.

    Each page's text is stripped of leading/trailing whitespace, then
    pages are joined with a blank line ("\\n\\n") between them — the
    minimal normalization needed to combine multiple pages into one
    readable string, and nothing beyond that (no whitespace
    collapsing within a page, no cleaning, no reformatting).

    A valid PDF with no extractable text on any page returns an empty
    string — this is NOT a ParseError. Conflating "nothing to extract"
    with "couldn't extract" would hide a real distinction a future
    caller may care about (e.g. flagging scanned/image-only PDFs for
    OCR later, versus a genuinely broken upload).

    Raises:
        UnsupportedFormatError: the file's extension isn't `.pdf`.
        ParseError: the path doesn't exist, or the file has a `.pdf`
            extension but PyMuPDF cannot open it (empty file or
            corrupted/invalid PDF content).
    """
    if file_path.suffix.lower() != _SUPPORTED_EXTENSION:
        raise UnsupportedFormatError(
            f"Unsupported file format {file_path.suffix.lower() or '(none)'!r}; "
            f"only {_SUPPORTED_EXTENSION} is supported by this parser."
        )

    if not file_path.is_file():
        raise ParseError("The file could not be found.")

    try:
        # pymupdf.FileDataError covers both a corrupted/invalid PDF
        # and pymupdf.EmptyFileError (a subclass of it, for a
        # zero-byte file) — one except clause legitimately covers
        # both cases, verified directly against the actual library
        # rather than assumed.
        with pymupdf.open(file_path) as document:
            pages_text = [page.get_text().strip() for page in document]
    except pymupdf.FileDataError as exc:
        raise ParseError("The file is not a valid PDF or is corrupted.") from exc

    return "\n\n".join(pages_text)
