"""
Tests for app.services.parse_service.

Independent of the DB, HTTP, and every fixture in conftest.py —
parse_service takes a filesystem Path and returns text or raises,
nothing else, so these tests need nothing beyond pytest's own
tmp_path. PDF fixtures are generated on the fly with PyMuPDF itself
(the same library under test) rather than committing binary files to
the repo — matches storage_service's own test style of using real
files, not mocks.
"""
from pathlib import Path

import pymupdf
import pytest

from app.services import parse_service


def _make_pdf(path: Path, pages_text: list[str]) -> None:
    """Create a real, valid PDF at `path` with one page per string in pages_text."""
    document = pymupdf.open()
    for text in pages_text:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


# --- Test 1: basic valid PDF ---


def test_extract_text_basic_valid_pdf(tmp_path):
    pdf_path = tmp_path / "basic.pdf"
    _make_pdf(pdf_path, ["Hello, ResearchPilot."])

    result = parse_service.extract_text(pdf_path)

    assert "Hello, ResearchPilot." in result


# --- Test 2: multi-page PDF, order preserved ---


def test_extract_text_multi_page_preserves_order(tmp_path):
    pdf_path = tmp_path / "multipage.pdf"
    _make_pdf(pdf_path, ["Page one content", "Page two content", "Page three content"])

    result = parse_service.extract_text(pdf_path)

    assert "Page one content" in result
    assert "Page two content" in result
    assert "Page three content" in result
    assert (
        result.index("Page one content")
        < result.index("Page two content")
        < result.index("Page three content")
    )


# --- Test 3: valid PDF with little/no text — NOT a ParseError ---


def test_extract_text_blank_page_returns_empty_string_not_error(tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    _make_pdf(pdf_path, [""])  # one page, no text inserted — valid PDF, no content

    result = parse_service.extract_text(pdf_path)

    assert result == ""


# --- Test 4: corrupted PDF ---


def test_extract_text_corrupted_pdf_raises_parse_error(tmp_path):
    pdf_path = tmp_path / "corrupted.pdf"
    pdf_path.write_bytes(b"this is not a real pdf, just garbage bytes")

    with pytest.raises(parse_service.ParseError):
        parse_service.extract_text(pdf_path)


# --- Test 5: empty file ---


def test_extract_text_empty_file_raises_parse_error(tmp_path):
    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(b"")

    with pytest.raises(parse_service.ParseError):
        parse_service.extract_text(pdf_path)


# --- Test 6: nonexistent file ---


def test_extract_text_nonexistent_file_raises_parse_error(tmp_path):
    pdf_path = tmp_path / "does_not_exist.pdf"

    with pytest.raises(parse_service.ParseError):
        parse_service.extract_text(pdf_path)


# --- Test 7: unsupported extension ---


def test_extract_text_rejects_docx(tmp_path):
    docx_path = tmp_path / "notes.docx"
    docx_path.write_bytes(b"pretend docx content")

    with pytest.raises(parse_service.UnsupportedFormatError):
        parse_service.extract_text(docx_path)


def test_extract_text_rejects_txt(tmp_path):
    txt_path = tmp_path / "readme.txt"
    txt_path.write_text("plain text content")

    with pytest.raises(parse_service.UnsupportedFormatError):
        parse_service.extract_text(txt_path)


# --- Test 8: no HTTP/database dependency ---


def test_extract_text_is_callable_directly_with_just_a_path(tmp_path):
    """
    No fixtures beyond pytest's own tmp_path are needed here — proof
    this service has zero DB/HTTP/app dependency, unlike
    test_documents_api.py, which needs the client/db_session fixtures
    from conftest.py.
    """
    pdf_path = tmp_path / "standalone.pdf"
    _make_pdf(pdf_path, ["Standalone check"])

    result = parse_service.extract_text(pdf_path)

    assert isinstance(result, str)
    assert "Standalone check" in result


# --- Normalization behavior (documented in extract_text's docstring) ---


def test_extract_text_joins_pages_with_blank_line(tmp_path):
    pdf_path = tmp_path / "joined.pdf"
    _make_pdf(pdf_path, ["First", "Second"])

    result = parse_service.extract_text(pdf_path)

    assert "First\n\nSecond" in result


# --- Windows file-handle regression (R2 stabilization) ---


def test_extract_text_does_not_hold_file_handle_after_corrupt_pdf(tmp_path):
    """
    Regression test for the real cause of the Windows [WinError 32]
    temp-file failure.

    Previously extract_text() used `with pymupdf.open(file_path)`,
    which leaks an OS file handle when the PDF is corrupt: open()
    raises during construction, so the `with` body is never entered
    and close() never runs. On Windows that left the file locked and
    the caller's temp-file cleanup failed.

    Deleting the file immediately after the ParseError is what proves
    no handle is held. Be aware of the platform asymmetry, so this
    test is not mistaken for stronger evidence than it is:

      - On Windows it is a genuine regression test. Reintroducing the
        path-mode open makes unlink() raise PermissionError here,
        because the exception's traceback keeps the partially
        constructed Document (and its handle) alive.
      - On POSIX it will pass either way, since deleting a file with
        an open handle is perfectly legal. It is kept running on Linux
        only to confirm the success/failure paths still behave, NOT as
        proof the leak is absent.

    The actual guarantee comes from construction rather than from this
    assertion: extract_text() now passes bytes to PyMuPDF, so PyMuPDF
    never opens the file and there is no handle to leak on any
    platform.
    """
    pdf_path = tmp_path / "corrupted.pdf"
    pdf_path.write_bytes(b"this is not a real pdf, just garbage bytes")

    with pytest.raises(parse_service.ParseError):
        parse_service.extract_text(pdf_path)

    pdf_path.unlink()  # must not raise PermissionError
    assert not pdf_path.exists()


def test_extract_text_does_not_hold_file_handle_after_success(tmp_path):
    """Same guarantee on the success path: a parsed file must be
    immediately deletable, with no lingering PyMuPDF handle."""
    pdf_path = tmp_path / "valid.pdf"
    _make_pdf(pdf_path, ["Some text"])

    result = parse_service.extract_text(pdf_path)
    assert "Some text" in result

    pdf_path.unlink()  # must not raise PermissionError
    assert not pdf_path.exists()
