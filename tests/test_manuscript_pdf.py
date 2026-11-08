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
    # Derived from the shipped results rather than hard-coded: a literal here would
    # silently stop tracking the table it is supposed to be checking.
    import json

    overall = json.loads(
        (REPO / "results" / "survey.json").read_text(encoding="utf-8")
    )["tag_absence"]["overall"]
    n = overall["n_series_readable"]
    for cell in (
        f"{overall['without_ctdivol_tag']} / {n} ({100 * overall['ctdivol_absence_rate']:.1f}%)",
        f"{overall['surveyable']} ({100 * overall['surveyable_rate']:.1f}%)",
    ):
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


def test_the_pdf_builder_runs_clean(tmp_path):
    """End to end, so a refactor of the renderer cannot break the build silently.

    ``--out`` matters: without it the builder writes over ``paper/ctdose-core_jacmp.pdf``,
    the file that gets uploaded. Running the suite then rewrites the submitted artefact
    with new PDF internals every time, so ``git status`` is never clean and a real change
    to the manuscript is indistinguishable from test churn.
    """
    pytest.importorskip("reportlab", reason="reportlab is needed to build the PDF")
    before = PDF.read_bytes() if PDF.exists() else None
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "build_manuscript_pdf.py"),
            "--out",
            str(tmp_path / "built.pdf"),
        ],
        cwd=REPO, capture_output=True, text=True,
    )
    assert (PDF.read_bytes() if PDF.exists() else None) == before, (
        "building the PDF must not touch the committed submission file"
    )
    assert result.returncode == 0, result.stderr
    assert "table(s)" in result.stdout
    assert "1 table(s)" in result.stdout, "Table 1 was not recognised as a table"


# --- the double-masked review build ----------------------------------------------------

MASKED_PDF = REPO / "paper" / "ctdose-core_jacmp_masked.pdf"


def test_the_masked_review_pdf_names_nobody():
    """The article file goes to a double-masked review; one leak defeats the whole build.

    The masking is a scripted transform rather than a hand-edit precisely so it can be
    re-checked after every revision. This asserts the property the transform exists for,
    against the text the reviewer actually sees.
    """
    if not MASKED_PDF.exists():
        pytest.skip("masked review PDF not built")
    pytest.importorskip("pypdf", reason="pypdf is needed to read the PDF back")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_masked_manuscript", REPO / "tools" / "build_masked_manuscript.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    leaked = module.verify(MASKED_PDF)
    assert not leaked, f"the masked PDF still identifies the author: {leaked}"


def test_the_masked_pdf_is_current_with_the_manuscript():
    """A stale masked build submits the previous revision under the current title."""
    if not MASKED_PDF.exists():
        pytest.skip("masked review PDF not built")
    assert MASKED_PDF.stat().st_mtime >= SOURCE.stat().st_mtime, (
        "paper/ctdose-core_jacmp_masked.pdf is older than the manuscript; "
        "re-run tools/build_masked_manuscript.py"
    )


def test_the_structured_abstract_carries_the_headings_jacmp_requires():
    """JACMP returned the manuscript for these exact five headings, in this order."""
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("## Abstract")
    end = text.index("\n---\n", start)
    abstract = text[start:end]
    headings = re.findall(r"\*\*(\w+):\*\*", abstract)
    from tests.test_technote_consistency import ABSTRACT_HEADINGS

    assert headings == list(ABSTRACT_HEADINGS), (
        f"the abstract headings are {headings}; the editorial office asked for "
        f"{list(ABSTRACT_HEADINGS)} in its Initial Quality Check of 2026-07-28"
    )
    # The submission form's abstract box is filled from this file, so it must not drift
    # from the manuscript.
    box = (REPO / "paper" / "abstract_masked_structured.txt")
    if box.exists():
        for para in box.read_text(encoding="utf-8").split("\n\n"):
            body = para.partition(":")[2].strip()
            if body:
                assert body in abstract, (
                    "paper/abstract_masked_structured.txt has drifted from the "
                    f"manuscript abstract: {body[:70]!r}"
                )
