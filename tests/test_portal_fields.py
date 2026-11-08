"""The text that gets pasted into the submission form.

Every other gate in this repository reads a file. The abstract box and the cover-letter
box are typed into a form, so no build reaches them and no file-reading gate sees them.
That cost IORN-007 a round: two of five Novel Scientific Points went to the journal
carrying claims the manuscript had withdrawn four hours earlier, because the manuscript
was rebuilt and the typed field was not. Editorial Manager gives an author no way to
amend such a field after submission, so there is no recovering from it in the portal.

These tests cover what can be checked before the pasting: that the pasteable text exists,
is built from the manuscript that is actually being submitted, is plain text in the shape
a textarea wants, and -- once recorded -- still matches the build.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
sys.path.insert(0, str(PAPER))

import record_portal_fields as portal  # noqa: E402

from tests.test_technote_consistency import ABSTRACT_HEADINGS  # noqa: E402

ABSTRACT = PAPER / "build" / "abstract_for_portal.txt"
COVER = PAPER / "build" / "cover_letter_for_portal.txt"


def built(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.name} is not built; run paper/build_portal_text.py")
    return path.read_text(encoding="utf-8")


def test_the_portal_abstract_is_the_technical_notes_not_the_research_articles():
    """The wrong abstract is in the repository, and it is the one to hand.

    paper/abstract_masked_structured.txt is the Research Article's abstract, 491 words.
    It is correct for the manuscript it belongs to and is checked against it. But it is
    the file an author reaches for at the portal, and pasting it would put a superseded
    500-word abstract on a Technical Note whose own abstract is 344 words.
    """
    text = built(ABSTRACT)
    words = len(text.split())
    assert words <= 500, f"the abstract box text is {words} words; the limit is 500"

    technote = (PAPER / "ctdose-core_jacmp_technote.md").read_text(encoding="utf-8")
    start = technote.index("## Abstract")
    abstract = technote[start : technote.index("\n---\n", start)]
    assert abs(words - len(abstract.split())) <= 5, (
        f"the abstract box text is {words} words and the Technical Note's abstract is "
        f"{len(abstract.split())}; they should be the same abstract"
    )

    superseded = PAPER / "abstract_masked_structured.txt"
    if superseded.is_file():
        assert len(superseded.read_text(encoding="utf-8").split()) != words, (
            "the abstract box text is the Research Article's abstract"
        )


def test_the_portal_abstract_carries_the_headings_the_editorial_office_asked_for():
    text = built(ABSTRACT)
    for heading in ABSTRACT_HEADINGS:
        assert re.search(rf"^{heading}:", text, flags=re.MULTILINE), (
            f"the abstract box text has no {heading!r} heading"
        )
    order = [h for h in ABSTRACT_HEADINGS if re.search(rf"^{h}:", text, re.MULTILINE)]
    positions = [text.index(f"{h}:") for h in order]
    assert positions == sorted(positions), "the headings are out of order"


def test_the_pasted_text_is_plain_and_one_paragraph_per_line():
    """A textarea takes no Markdown, and wraps for itself.

    The runbook's rule: one paragraph per line, blank line between. A letter
    hard-wrapped at 90 columns arrives with every line broken.
    """
    for path in (ABSTRACT, COVER):
        text = built(path)
        assert "**" not in text, f"{path.name} still carries Markdown emphasis"
        assert not re.search(r"^#{1,6}\s", text, flags=re.MULTILINE), (
            f"{path.name} still carries a Markdown heading"
        )
        assert not re.search(r"\[[^\]]+\]\([^)]+\)", text), (
            f"{path.name} still carries a Markdown link"
        )
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines, f"{path.name} is empty"
        # A hard-wrapped document has many lines near a common width; a paragraph-per-line
        # document has few long ones.
        assert max(len(ln) for ln in lines) > 200, (
            f"{path.name} looks hard-wrapped; the box wants one paragraph per line"
        )


def test_the_cover_letter_box_holds_this_rounds_letter():
    text = built(COVER)
    assert "second revision" in text or "Revision 2" in text or "revision 2" in text, (
        "the cover-letter box text is not this round's letter"
    )
    assert "To the Editors" not in text or text.startswith("To the Editors"), (
        "the letterhead should not be in the middle of the box text"
    )


def test_recorded_portal_fields_still_match_the_build():
    """Once pasted and recorded, the record must keep matching what is built.

    A mismatch is either "the build moved after you pasted" or "you pasted something
    else", and both want the same action: paste again, record again. Nothing is recorded
    until the author pastes, so this skips before then rather than failing.
    """
    ledger = portal.load()
    if not ledger:
        pytest.skip(
            "no portal field recorded yet; record each one as it is pasted with "
            "paper/record_portal_fields.py"
        )
    for field, source in portal.FIELDS.items():
        entry = ledger.get(field)
        if entry is None or not source.is_file():
            continue
        assert entry["entered_sha256"] == portal.digest(
            source.read_text(encoding="utf-8")
        ), (
            f"the portal field {field!r} does not match {source.name}: what was entered "
            f"on {entry['recorded_utc']} is not what the build now says"
        )
