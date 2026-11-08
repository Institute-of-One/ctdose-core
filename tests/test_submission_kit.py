"""The submission kit must be resolved from the build, not remembered.

A kit is written once and then consulted at every subsequent submission, which is the
worst possible combination: it is trusted most when it is most out of date. The previous
one was written on 2026-07-27 for the Research Article and survived a reclassification
and a full revision. By the time it was read again it asserted the wrong title, the wrong
running title, the wrong limits for the article type, the wrong word count, the wrong
reference count, the wrong files to upload -- it still named the superseded manuscript
and the three withdrawn figures -- a cover letter for a submission that no longer
existed, and an invoicing instruction that was the opposite of the one in force.

Everything in it that the repository knows is therefore checked against the repository.
What is left is judgement, which a test cannot hold.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
KIT = PAPER / "jacmp_submission_kit.md"

from tests.test_technote_consistency import (  # noqa: E402
    BRITISH_ENDINGS,
    BRITISH_STEMS,
    FIGURE_AND_TABLE_LIMIT,
    REFERENCE_LIMIT,
    US_SPELLING,
    WORD_LIMIT,
)


@pytest.fixture(scope="module")
def kit() -> str:
    if not KIT.is_file():
        pytest.skip("the submission kit is not written")
    return KIT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manuscript() -> str:
    return (PAPER / "ctdose-core_jacmp_technote.md").read_text(encoding="utf-8")


def test_the_kit_quotes_the_manuscript_title_and_running_title(kit, manuscript):
    title = manuscript.splitlines()[0].lstrip("# ").strip()
    assert title in kit, (
        "the kit gives a different title from the manuscript; the previous kit still "
        f"carried the Research Article's. Expected:\n  {title}"
    )
    running = re.search(r"\*\*Running title:\*\* (.+)", manuscript).group(1).strip()
    assert running in kit, f"the kit's running title is stale; expected {running!r}"

    keywords = re.search(r"\*\*Keywords:\*\* (.+)", manuscript).group(1).strip()
    assert keywords in kit, "the kit and the manuscript disagree on the keywords"


def test_the_kit_quotes_the_real_counts_and_the_real_limits(kit, manuscript):
    words = len(manuscript.split())
    figures = len(set(re.findall(r"\*\*Figure (\d)\.\*\*", manuscript)))
    tables = len(set(re.findall(r"\*\*Table (\d)\.\*\*", manuscript)))
    refs = len(re.findall(r"^(\d+)\. ", manuscript, flags=re.MULTILINE))

    for value, what in (
        (words, "the word count"),
        (WORD_LIMIT, "the word limit"),
        (figures, "the figure count"),
        (tables, "the table count"),
        (FIGURE_AND_TABLE_LIMIT, "the display-item limit"),
        (refs, "the reference count"),
        (REFERENCE_LIMIT, "the reference limit"),
    ):
        assert re.search(rf"\b{value}\b", kit), f"the kit does not state {what} ({value})"

    # Bound to the compliance row, not merely present somewhere. Every one of these
    # numbers appears more than once in this document -- in the compliance table, in the
    # table of what changed this revision, and in the note explaining that revision 1
    # used the wrong limit -- so "is 13 anywhere in the kit" stays true when the
    # compliance row is left at the previous revision's 11. Where a fact is written in
    # several places, bind the needle to the place that asserts it.
    def compliance_row(prefix: str) -> str:
        row = next(
            (line for line in kit.splitlines() if line.startswith(f"| {prefix}")), None
        )
        assert row, f"the kit has no compliance row beginning {prefix!r}"
        return row

    for prefix, limit, actual, what in (
        ("Total word count", WORD_LIMIT, words, "words"),
        ("References", REFERENCE_LIMIT, refs, "references"),
        ("Figures + tables", FIGURE_AND_TABLE_LIMIT, figures + tables, "display items"),
    ):
        row = compliance_row(prefix)
        assert re.search(rf"\b{limit}\b", row), (
            f"the {what} compliance row states the wrong limit; it is {limit}. "
            f"Row: {row!r}"
        )
        assert re.search(rf"\b{actual}\b", row), (
            f"the {what} compliance row is stale; the manuscript has {actual}. "
            f"Row: {row!r}"
        )


def test_the_kit_names_the_files_that_are_actually_uploaded(kit):
    """The name of the file to upload is the thing a kit most needs to be right about."""
    must_name = [
        "paper/ctdose-core_jacmp_technote_masked.pdf",
        "paper/ctdose-core_jacmp_technote_tracked_changes.pdf",
        "paper/response_to_editor_jacmp_r2.pdf",
        "paper/title_page_jacmp.pdf",
        "paper/ctdose-core_jacmp_technote_supplement.pdf",
        # The pasted boxes name the *built* plain text, not the Markdown source: a
        # textarea takes no Markdown, and the abstract has a wrong-version twin sitting
        # next to it in paper/.
        "paper/build/cover_letter_for_portal.txt",
        "paper/build/abstract_for_portal.txt",
    ]
    for name in must_name:
        assert name in kit, f"the kit does not name {name} among the files to upload"
        assert (REPO / name).is_file(), f"{name} is named in the kit but does not exist"

    # Figures 1-3 are uploaded; 4-6 belong to the withdrawn organ layer.
    for n in (1, 2, 3):
        matches = list((PAPER / "figures").glob(f"fig{n}_*.png"))
        assert matches, f"Figure {n} has no image file"
        assert matches[0].name in kit, f"the kit does not name {matches[0].name}"

    uploaded_section = kit[kit.index("## 3. Files to upload"):kit.index("## 4.")]
    for n in (4, 5, 6):
        for path in (PAPER / "figures").glob(f"fig{n}_*.png"):
            assert path.name not in uploaded_section, (
                f"{path.name} belongs to the withdrawn organ layer and must not be "
                f"listed for upload"
            )
    # Nor the superseded Research Article, nor the unmasked canonical copy.
    for forbidden in ("| `paper/ctdose-core_jacmp.pdf`", "| `paper/ctdose-core_jacmp.md`"):
        assert forbidden not in uploaded_section, (
            "the kit lists the superseded Research Article among the files to upload"
        )


def test_the_kit_does_not_carry_a_superseded_cover_letter(kit):
    """A kit holding its own copy of the letter is a letter that will be sent stale.

    The previous kit embedded the whole Research Article cover letter, addressed to a
    submission that no longer existed and written in the authorial plural. The letter
    lives in its own file now and the kit points at it.
    """
    assert "Dear Editors," not in kit, (
        "the kit embeds a cover letter; it should point at the built portal text so "
        "there is one copy to keep current"
    )
    assert "cover_letter_for_portal.txt" in kit
    # And it must warn off the Research Article's abstract, which is the file nearest to
    # hand at the portal and 147 words too long for this submission.
    assert "abstract_masked_structured.txt" in kit and "Do not paste" in kit, (
        "the kit must name the superseded abstract and say not to paste it"
    )


def test_the_kit_gives_the_invoicing_instruction_in_force(kit):
    """Settled previously, and the previous kit said the opposite."""
    section = kit[kit.index("## 6."):kit.index("## 7.")]
    assert "company card" in section
    assert "Do not ask Wiley to re-address" in section or (
        "do not try to get the bill-to changed" in section.lower()
    )
    assert "Attention: accounts payable" not in section, (
        "the superseded instruction to re-address the invoice is still here"
    )


def test_the_kit_does_not_claim_an_ai_checklist_that_does_not_apply(kit):
    """TotalSegmentator went out with the organ layer at revision 1."""
    assert "TotalSegmentator" not in kit, (
        "the withdrawn organ layer is still named in the kit"
    )
    if "CLAMP" in kit:
        assert "Not applicable" in kit or "not applicable" in kit, (
            "the CLAMP checklist is only mentioned to say it does not apply"
        )


def test_the_kit_declares_what_it_could_not_verify(kit):
    """Carried over from the gate this file replaces, which required a [verify] marker.

    The point survives the change of form: a kit states facts about the journal that
    change without notice, and the ones that were not confirmed from a primary source
    have to say so rather than read like the rest. The APC is the live example -- it is
    payable at acceptance, months from now, and the figure in the previous kit was
    already a year old.
    """
    section = kit[kit.index("## 6."):kit.index("## 7.")]
    row = next(
        line for line in section.splitlines() if line.startswith("| Article Processing")
    )
    assert "verify" in row.lower(), (
        "the APC row states a figure as though it were confirmed; it is payable at "
        "acceptance and must be checked then"
    )
    assert not re.search(r"USD\s*[\d,]+", section), (
        "a specific APC figure has been written back into the kit; it goes stale and "
        "the previous kit's was a year old when it was last read"
    )


def test_the_kit_carries_the_affiliation_and_the_licences(kit):
    """Also carried over: the one affiliation, and the two licences, stated correctly."""
    assert "Institute of One, LISIT Co., Ltd., Tokyo, Japan" in kit
    assert "CC BY" in kit and "MIT" in kit
    for forbidden in ("National Cancer Center", "NCC", "国立がん研究センター"):
        assert forbidden not in kit, f"{forbidden!r} must not appear in the kit"


def test_the_kit_names_the_abstract_headings_the_editorial_office_asked_for(kit, manuscript):
    """And names them in order, from the one constant the gates share.

    The kit told the author to paste an abstract "with the five headings the guidelines
    specify", and listed the guidelines page's plural. The editorial office had named
    the singular, in writing, as the reason this manuscript was returned. A kit is read
    at the moment of pasting, so a wrong heading list there is a wrong heading list in
    the portal.
    """
    from tests.test_technote_consistency import ABSTRACT_HEADINGS

    section = kit[: kit.index("## 2.")]
    for heading in ABSTRACT_HEADINGS:
        assert re.search(rf"\b{heading}\b", section), (
            f"the kit does not name the abstract heading {heading!r}"
        )
    # The plural must not come back, in the kit or in the manuscript.
    assert "Conclusions." not in section and "Results, Conclusions" not in section, (
        "the kit lists the Author Guidelines page's plural; the editorial office named "
        "the singular for this manuscript"
    )
    abstract = manuscript[
        manuscript.index("## Abstract") : manuscript.index(
            "\n---\n", manuscript.index("## Abstract")
        )
    ]
    assert re.findall(r"\*\*(\w+):\*\*", abstract) == list(ABSTRACT_HEADINGS)


def test_the_kit_points_at_the_runbook_gate(kit):
    assert "record_runbook_read.py" in kit
    assert "§2 and §11" in kit or "section 2 and section 11" in kit.lower()


def test_the_kit_uses_us_spelling(kit):
    found = sorted(
        b for b in US_SPELLING if re.search(rf"\b{b}\b", kit, flags=re.IGNORECASE)
    )
    for stem in BRITISH_STEMS:
        pattern = rf"\b{stem}(?:{'|'.join(BRITISH_ENDINGS)})\b"
        found += [m.group(0) for m in re.finditer(pattern, kit, flags=re.IGNORECASE)]
    assert not found, f"British spellings in the submission kit: {sorted(set(found))}"


def test_the_kit_counts_the_build_steps_and_the_test_suite(kit):
    """A number the kit states about the build must come from the build.

    "20 stages and all gates green" was true of a build that had 22 stages and 19 gates
    in another repository of this programme, and a stage that silently stopped running
    looked like a complete build. Here the kit says how many artefacts the build ships.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_all", PAPER / "build_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert re.search(rf"\b{len(module.ARTEFACTS)}\b", kit), (
        f"the kit does not state how many artefacts the build ships "
        f"({len(module.ARTEFACTS)})"
    )
