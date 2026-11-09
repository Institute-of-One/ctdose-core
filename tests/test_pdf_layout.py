"""Layout defects that only exist on the printed page.

Every other gate in this repository reads Markdown, where there are no pages, no fonts
and no line breaks. These three defects were all found by rendering the built PDF and
looking at it, which is the step the submission runbook insists on and the step that is
easiest to skip:

* **a reported negative number drawn with a hyphen.** The source carries U+2212 for
  exactly this reason -- a hyphen is a line-break opportunity, so "-59.4%" can be split
  across two lines -- and the PDF builder was substituting it back to a hyphen
  unconditionally, because the base-14 fonts cannot draw it. With a TrueType family
  embedded they can, and the substitution was undoing the fix in the source.
* **a table caption stranded at the foot of a page** with its table overleaf. "Table 2."
  sat alone at the bottom of page 9.
* **a heading left as the last line of a page.**

They are checked on the artefacts that are uploaded, not on the canonical sources.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF is needed to read the built PDFs")

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"

#: Everything that goes to the journal.
UPLOADED = [
    "ctdose-core_jacmp_technote_masked.pdf",
    "ctdose-core_jacmp_technote_supplement.pdf",
    "ctdose-core_jacmp_technote_tracked_changes.pdf",
    "response_to_editor_jacmp_r2.pdf",
    "title_page_jacmp.pdf",
]

#: A negative number in running text: a hyphen that begins a token and is followed by
#: digits. Anything with a digit or a letter in front of the hyphen is a range or an
#: identifier -- "L67-69", "2026-10019A", "IEC 60601-2-44", "TG-204", "GPL-3.0",
#: "doi:10.1055/s-2002-35937" -- and the first version of this gate reported all of
#: them. A rule that fires on things that are not defects is a rule that gets ignored.
NEGATIVE_WITH_HYPHEN = re.compile(r"(?:(?<=\s)|(?<=\())-\d")


def pages(name: str) -> list[str]:
    path = PAPER / name
    if not path.is_file():
        pytest.skip(f"{name} is not built")
    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


def _page_opens_with_an_image(name: str, page_number: int) -> bool:
    """Whether the page after ``page_number`` (1-based) begins with a picture.

    A rasterised equation is an image too, so "does the document contain images" is too
    blunt a question; what matters is whether the thing immediately overleaf is the
    figure the caption was introducing.
    """
    with fitz.open(PAPER / name) as doc:
        if page_number >= doc.page_count:
            return False
        page = doc[page_number]  # 0-based index of the *next* page
        images = page.get_images()
        if not images:
            return False
        top_image = min(
            (r.y0 for info in images for r in page.get_image_rects(info[0])),
            default=None,
        )
        if top_image is None:
            return False
        text_tops = [
            span["bbox"][1]
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"].strip() and not span["text"].strip().isdigit()
        ]
        return not text_tops or top_image <= min(text_tops) + 2.0


@pytest.mark.parametrize("name", UPLOADED)
def test_reported_negative_numbers_use_a_true_minus_sign(name):
    """U+2212, not a hyphen, so Word and the portal cannot break the line after it."""
    offenders = []
    for number, text in enumerate(pages(name), 1):
        for match in NEGATIVE_WITH_HYPHEN.finditer(text):
            start = max(0, match.start() - 30)
            context = " ".join(text[start : match.end() + 12].split())
            offenders.append(f"p{number}: ...{context!r}")
    assert not offenders, (
        "a negative number is drawn with a hyphen, which is a line-break opportunity; "
        "write U+2212 in the source:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("name", UPLOADED)
def test_no_table_or_figure_caption_is_stranded_from_what_it_labels(name):
    """A caption must share a page with the thing it introduces.

    The caption of Table 2 was the last line of page 9 and the table began on page 10.
    Nothing read the Markdown could see it, because a page does not exist there.
    """
    for number, text in enumerate(pages(name), 1):
        # Drop the marginal line numbers and the page-number footer: both are digits on
        # their own line, and leaving them in is what made the first version of this
        # gate pass on the real defect. "Is there anything after the caption?" was true,
        # because the page number came after it.
        lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.strip().isdigit()
        ]
        if not lines:
            continue
        for position, line in enumerate(lines):
            match = re.match(r"^(Table|Figure)\s+(\d+)\.", line)
            if not match:
                continue
            kind, index = match.group(1), match.group(2)
            if lines[position + 1 :]:
                continue  # something follows it on this page
            # Nothing follows it. Whether that is a defect depends on what it labels.
            if kind == "Figure":
                # A figure caption is separated from its figure only if the figure is
                # overleaf. If the next page opens with text, the caption is simply
                # wrapping -- which is what happens in the tracked-changes document,
                # where the figures are dropped because a diff of images says nothing.
                if not _page_opens_with_an_image(name, number):
                    continue
                # And the convention here is caption under image, so an image earlier
                # on this page means the pair is already together.
                if any(
                    re.search(rf"Figure {index}\b", earlier)
                    for earlier in lines[:position]
                ):
                    continue
            pytest.fail(
                f"{name} page {number}: the caption {line[:70]!r} is the last thing on "
                f"the page, so what it labels begins overleaf"
            )


#: Which Markdown each uploaded PDF is built from, so the gate below can ask the source
#: what the headings actually are instead of recognising them by shape.
SOURCE_OF = {
    "ctdose-core_jacmp_technote_masked.pdf": "ctdose-core_jacmp_technote_masked.md",
    "ctdose-core_jacmp_technote_supplement.pdf": "ctdose-core_jacmp_technote_supplement.md",
    "ctdose-core_jacmp_technote_tracked_changes.pdf": (
        "ctdose-core_jacmp_technote_tracked_changes.md"
    ),
    "response_to_editor_jacmp_r2.pdf": "response_to_editor_jacmp_r2.md",
    "title_page_jacmp.pdf": "title_page_jacmp.md",
}


def _headings_of(name: str) -> set[str]:
    """The document's real headings, read from the Markdown it was built from.

    The first version of this gate recognised a heading by shape -- a number, a space,
    then a word -- and that shape is also a table cell. It reported the row
    "Supplementary Material | 1 file | 1 file, one section added" in the point-by-point
    response as an orphaned heading, which it is not. A rule that fires on things that
    are not defects is a rule that gets switched off, so the headings are now resolved
    from the source: whatever the Markdown marks with ``#`` is a heading and nothing else
    is.
    """
    source = PAPER / SOURCE_OF[name]
    if not source.is_file():
        pytest.skip(f"{SOURCE_OF[name]} is not in this checkout")
    found = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if not match:
            continue
        # The rendered page carries no emphasis marks, and the tracked-changes document
        # wraps its headings in its own diff markers.
        plain = re.sub(r"[*_`]|~~|\+\+", "", match.group(1))
        found.add(" ".join(plain.split()))
    return found


@pytest.mark.parametrize("name", UPLOADED)
def test_no_heading_is_the_last_line_of_a_page(name):
    """Word does this by default and so did this renderer; both need telling."""
    headings = _headings_of(name)
    if not headings:
        # The title page is a flat block of labelled metadata with no headings at all,
        # so it has nothing to orphan. That the gate is not vacuous across the set is
        # asserted separately, below.
        pytest.skip(f"{SOURCE_OF[name]} has no headings")
    for number, text in enumerate(pages(name), 1):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # Drop the marginal line numbers and the page-number footer, both digits alone.
        lines = [ln for ln in lines if not ln.isdigit()]
        if not lines:
            continue
        if " ".join(lines[-1].split()) in headings:
            pytest.fail(
                f"{name} page {number} ends on the heading {lines[-1]!r}, with "
                f"its text overleaf"
            )


def test_the_orphaned_heading_gate_is_not_vacuous():
    """At least the manuscript and the supplement must contribute headings to check.

    The gate above skips a document with no headings, which is right for the title page
    and would be a silent hole if every document took that branch -- so the documents
    that must have headings are named here, and a build that stopped emitting them fails
    rather than skipping.
    """
    must_have = [
        "ctdose-core_jacmp_technote_masked.pdf",
        "ctdose-core_jacmp_technote_supplement.pdf",
        "response_to_editor_jacmp_r2.pdf",
    ]
    for name in must_have:
        if not (PAPER / SOURCE_OF[name]).is_file():
            continue
        headings = _headings_of(name)
        assert len(headings) >= 4, (
            f"{SOURCE_OF[name]} yields {len(headings)} headings; the orphan gate would "
            f"check almost nothing on it"
        )


@pytest.mark.parametrize("name", UPLOADED)
def test_no_page_is_blank_or_nearly_blank(name):
    for number, text in enumerate(pages(name), 1):
        body = " ".join(text.split())
        assert len(body) > 120, (
            f"{name} page {number} is blank or nearly blank ({len(body)} characters)"
        )


def test_the_manuscript_line_numbering_is_continuous_from_the_first_page():
    """One counter over the whole document, which is what reviewers cite."""
    path = PAPER / "ctdose-core_jacmp_technote_masked.pdf"
    if not path.is_file():
        pytest.skip("the masked manuscript is not built")
    numbers: list[int] = []
    with fitz.open(path) as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        value = span["text"].strip()
                        if value.isdigit() and len(value) <= 4 and span["bbox"][0] < 60:
                            numbers.append(int(value))
    assert numbers, "no marginal line numbers found"
    assert numbers[0] == 1, f"line numbering starts at {numbers[0]}, not 1"
    restarts = [b for a, b in zip(numbers, numbers[1:], strict=False) if b <= a]
    assert not restarts, f"line numbering restarts {len(restarts)} time(s)"
    gaps = [(a, b) for a, b in zip(numbers, numbers[1:], strict=False) if b - a != 1]
    assert not gaps, f"line numbering skips: {gaps[:5]}"


#: Markup that must never survive into a rendered page: the math delimiters, the bold
#: and strike and insert markers, and a LaTeX macro with its braces.
UNRENDERED_MARKUP = re.compile(r"\$|\*\*|~~|\+\+|\\[a-zA-Z]+\{")

#: Punctuation that closes the word before it and therefore cannot begin a line.
ORPHANED_PUNCTUATION = re.compile(r"^[.,;:)%]")


@pytest.mark.parametrize("name", UPLOADED)
def test_no_table_is_printed_as_markdown(name: str) -> None:
    """A table must be typeset as a table in every file that is uploaded.

    There was a gate for this, on the superseded Research Article's PDF only. The
    tracked-changes file -- the one the editor opens to see what changed -- printed both
    of its tables as raw pipe rows, alignment rows included, because the diff gives each
    row its own block and every block was joined with a blank line, so nothing
    downstream could see a table. An added row made it worse: wrapped whole in the
    insertion marker, it was not even a row any more.
    """
    offenders = [
        (number, line.strip()[:80])
        for number, text in enumerate(pages(name), 1)
        for line in text.splitlines()
        if line.strip().startswith("|") or re.match(r"^\s*\|?\s*:?-{3,}:?\s*\|", line)
    ]
    assert not offenders, (
        f"{len(offenders)} line(s) of {name} print Markdown table syntax: {offenders[:3]}"
    )


@pytest.mark.parametrize("name", UPLOADED)
def test_no_line_begins_with_the_punctuation_that_closes_the_word_before_it(
    name: str,
) -> None:
    """A run boundary is not a word boundary, and the wrapper used to think it was.

    The supplement printed a line ending ``39 of the 41`` -- a bold span -- and the next
    line beginning ``. The two exceptions``. The source has no space there; the full
    stop was simply in a different run. The same break was possible after an inline
    equation, which would have put a semicolon at the start of a line.
    """
    path = PAPER / name
    if not path.is_file():
        pytest.skip(f"{name} is not built")
    offenders = orphaned_punctuation_in(path)
    assert not offenders, (
        f"{len(offenders)} line(s) of {name} begin with punctuation that belongs to the "
        f"previous line: {offenders[:3]}"
    )


def orphaned_punctuation_in(path: Path) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            # An inline equation is an image, and the text after it is extracted as a
            # new line even though it is drawn on the same one. Those are artefacts of
            # reading the page, not defects on it, so a line whose own band has an
            # image to its left is passed over. A genuine break puts that image at the
            # end of the *previous* line, where it does not overlap this line's band.
            images = [
                r
                for info in page.get_images()
                for r in page.get_image_rects(info[0])
            ]
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    if not ORPHANED_PUNCTUATION.match(text):
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    beside = any(
                        r.x1 <= x0 + 2 and r.y0 < y1 and y0 < r.y1 for r in images
                    )
                    if not beside:
                        offenders.append((number, text))
    return offenders


#: A page holding fewer than this many lines of text, and no picture, is a page that
#: should not have been started. Measured against the documents as they stand: the
#: sparsest legitimate page is the manuscript's last, which carries a figure.
MINIMUM_LINES_ON_A_PAGE = 3


@pytest.mark.parametrize("name", [*UPLOADED, "cover_letter_jacmp_r2.pdf"])
def test_no_page_is_nearly_empty(name: str) -> None:
    """A page with a line or two on it means something was split that should not be.

    The cover letter ran to four pages with two lines of the signature block alone on
    the last: the letterhead had been spaced out to fix a run-on, and the signature went
    over the break. Both halves of that -- a nearly blank page, and a signature block
    split across pages -- are defects the SOP names.
    """
    path = PAPER / name
    if not path.is_file():
        pytest.skip(f"{name} is not built")
    sparse: list[tuple[int, int]] = []
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            lines = [
                line
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                if "".join(s["text"] for s in line["spans"]).strip()
                and not "".join(s["text"] for s in line["spans"]).strip().isdigit()
            ]
            if len(lines) < MINIMUM_LINES_ON_A_PAGE and not page.get_images():
                sparse.append((number, len(lines)))
    assert not sparse, (
        f"{name} has a nearly empty page: {sparse} (page, lines of text). Something is "
        f"split across the break that should be kept together."
    )


def test_the_nearly_empty_page_gate_is_not_vacuous(tmp_path: Path) -> None:
    """Draw a document that ends on a two-line page and require that it is reported."""
    import importlib.util
    import sys
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "bmp_sparse", REPO / "tools" / "build_manuscript_pdf.py"
    )
    bmp = importlib.util.module_from_spec(spec)
    sys.modules["bmp_sparse"] = bmp
    spec.loader.exec_module(bmp)

    # Long enough to fill one page and spill a couple of lines onto a second.
    filler = "The reconstruction reads the technique from the header. " * 46
    out = tmp_path / "sparse.pdf"
    with tempfile.TemporaryDirectory() as work:
        lines = bmp.build_lines(
            f"# Fixture\n\n{filler}\n\nSincerely,\n\nShuji Yamamoto\n",
            Path(work), include_figures=False,
        )
        bmp.draw(lines, out, "fixture")

    with fitz.open(out) as doc:
        counts = [
            len([
                line
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                if "".join(s["text"] for s in line["spans"]).strip()
                and not "".join(s["text"] for s in line["spans"]).strip().isdigit()
            ])
            for page in doc
        ]
    assert len(counts) > 1, f"the fixture fitted on one page: {counts}"
    assert min(counts) < MINIMUM_LINES_ON_A_PAGE, (
        f"the fixture has no nearly empty page, so it tests nothing: {counts}"
    )


def test_the_orphaned_punctuation_gate_is_not_vacuous(tmp_path: Path) -> None:
    """Draw a page that has the defect, and require that it is reported.

    Every uploaded file passes this gate, which on its own says nothing. What is
    injected here is the defect **as it appears on the page** -- a line beginning with
    the full stop that closed the previous one -- rather than the wrapper bug that
    produced it. Reproducing that bug would need the bold span to end within one
    full stop's width of the measure, which no filler of a fixed word happens to do;
    ``tests/test_inline_markup.py`` pins the wrapper itself, at a width that was
    measured for exactly that.
    """
    import importlib.util
    import sys
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "bmp_orphan", REPO / "tools" / "build_manuscript_pdf.py"
    )
    bmp = importlib.util.module_from_spec(spec)
    sys.modules["bmp_orphan"] = bmp
    spec.loader.exec_module(bmp)

    source = (
        "# Fixture\n\n"
        "It could be computed for 39 of the 41\n\n"
        ". The two exceptions are a finding in themselves.\n"
    )
    out = tmp_path / "orphan.pdf"
    with tempfile.TemporaryDirectory() as work:
        lines = bmp.build_lines(source, Path(work), include_figures=False)
        bmp.draw(lines, out, "fixture")

    found = orphaned_punctuation_in(out)
    assert any(text.startswith(".") for _, text in found), (
        f"a line beginning with a full stop was not reported: {found}"
    )


@pytest.mark.parametrize("name", UPLOADED)
def test_no_markup_survives_into_the_rendered_page(name: str) -> None:
    """The source is Markdown; the reader must never see any of it.

    Found by looking at the pages, after every gate was green. The inline splitter is a
    flat alternation, so the first delimiter to open swallowed everything to its close
    and whatever was nested inside printed literally. Two uploaded files carried it: the
    Article File printed ``$w$ refutes, and does not select.`` where a bold sentence
    opened with an inline formula, and the tracked-changes file -- in which *every*
    insertion is wrapped in a marker -- delivered its entire new section to the editor as
    raw Markdown, asterisks, dollar signs and backslashes included.
    """
    offenders = [
        (number, line.strip())
        for number, text in enumerate(pages(name), 1)
        for line in text.splitlines()
        if UNRENDERED_MARKUP.search(line)
    ]
    assert not offenders, (
        f"{len(offenders)} line(s) of {name} show unrendered Markdown on the page: "
        f"{offenders[:3]}"
    )
