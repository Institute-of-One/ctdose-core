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
