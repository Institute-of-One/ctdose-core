"""The submission PDF must be a submittable artefact, not merely a file that was produced.

Two defects reached a circulated draft and neither was caught by anything: Table 1 was
emitted as literal Markdown pipes, and five characters outside WinAnsi -- including every
typographic minus sign -- rendered as black squares. Both are invisible to a test that
only checks the build exits zero, so the rendered document is inspected here.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "paper" / "ctdose-core_jacmp.md"
PDF = REPO / "paper" / "ctdose-core_jacmp.pdf"


@pytest.fixture(scope="module")
def pdf_text() -> str:
    pypdf = pytest.importorskip("pypdf", reason="pypdf is needed to inspect the PDF")
    if not PDF.exists():
        pytest.skip("submission PDF not built")
    reader = pypdf.PdfReader(str(PDF))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.fixture(scope="module")
def pdf_fonts() -> dict[str, bool]:
    pypdf = pytest.importorskip("pypdf", reason="pypdf is needed to inspect the PDF")
    if not PDF.exists():
        pytest.skip("submission PDF not built")
    reader = pypdf.PdfReader(str(PDF))
    fonts: dict[str, bool] = {}
    for page in reader.pages:
        for ref in (page.get("/Resources", {}).get("/Font", {}) or {}).values():
            font = ref.get_object()
            descriptor = font.get("/FontDescriptor")
            embedded = bool(descriptor) and any(
                key in descriptor.get_object()
                for key in ("/FontFile", "/FontFile2", "/FontFile3")
            )
            name = str(font.get("/BaseFont", "?"))
            fonts[name] = fonts.get(name, False) or embedded
    return fonts


def test_every_font_is_embedded(pdf_fonts):
    """Base-14 fonts are referenced, not embedded; several publishers reject that."""
    missing = sorted(name for name, embedded in pdf_fonts.items() if not embedded)
    assert not missing, f"fonts not embedded in the submission PDF: {missing}"


def test_no_markdown_table_syntax_survives_into_the_pdf(pdf_text):
    """Table 1 must be typeset, not printed as pipes and dashes."""
    assert "| ---" not in pdf_text
    assert not re.search(r"\|\s*Manufacturer\s*\|", pdf_text)


def test_the_table_content_is_present_as_text(pdf_text):
    """Typeset is not enough -- the cells must actually carry the data."""
    flat = re.sub(r"\s+", " ", pdf_text)
    for heading in (
        "Manufacturer",
        "CTDIvol absent",
        "Of those, reconstructable",
        "Conditional rate",
        "Recoverable by either route",
    ):
        assert heading in flat, f"Table 1 is missing the {heading!r} column"
    for cell in ("206 / 400 (51.5%)", "278 (69.5%)"):
        assert cell in flat, f"Table 1 is missing the {cell!r} total"


def test_the_source_contains_no_character_the_body_font_cannot_draw():
    """The guard that turns a silent black square into a failed build."""
    pytest.importorskip("reportlab", reason="reportlab is needed to build the PDF")
    sys.path.insert(0, str(REPO / "tools"))
    from build_manuscript_pdf import assert_encodable

    assert_encodable(SOURCE.read_text(encoding="utf-8"))


def test_the_pdf_carries_no_replacement_or_notdef_markers(pdf_text):
    """A glyph the font lacks extracts as U+FFFD or vanishes; neither belongs here."""
    assert "�" not in pdf_text, "the PDF contains replacement characters"


def test_the_data_statement_names_a_resolvable_archive_or_a_loud_placeholder():
    """The Zenodo DOI must be real, or obviously unfinished -- never a vague promise.

    'DOI to be assigned at acceptance' is wrong on the facts (Zenodo mints the DOI when
    the GitHub release is archived, not at acceptance) and is quiet enough to survive
    into a submitted PDF. A placeholder is fine; an unnoticeable one is not.
    """
    text = SOURCE.read_text(encoding="utf-8")
    assert "DOI to be assigned at acceptance" not in text

    real = re.search(r"10\.5281/zenodo\.\d+", text)
    placeholder = "PLACEHOLDER-REPLACE-BEFORE-SUBMISSION" in text
    assert real or placeholder, (
        "the data-availability statement must give a Zenodo DOI or an obvious placeholder"
    )
    if real:
        assert not placeholder, "both a real DOI and a placeholder are present"


def test_the_pdf_is_current_with_the_source():
    """A PDF older than the manuscript is the one that gets submitted by accident."""
    if not PDF.exists():
        pytest.skip("submission PDF not built")
    assert PDF.stat().st_mtime >= SOURCE.stat().st_mtime, (
        "paper/ctdose-core_jacmp.pdf is older than the manuscript; "
        "re-run tools/build_manuscript_pdf.py"
    )


def test_the_pdf_builder_runs_clean():
    """End to end, so a refactor of the renderer cannot break the build silently."""
    pytest.importorskip("reportlab", reason="reportlab is needed to build the PDF")
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_manuscript_pdf.py")],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "table(s)" in result.stdout
    assert "1 table(s)" in result.stdout, "Table 1 was not recognised as a table"
