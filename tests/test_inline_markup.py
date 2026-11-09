"""Nested inline markup, at the unit the page defect came from.

``tests/test_pdf_layout.py`` checks that no Markdown reaches a rendered page. That gate
is the one that matters, but it can only say *that* something leaked. These say where:
the inline splitter is a flat alternation, so the first delimiter to open swallows
everything up to its close, and what is nested inside it was emitted as literal text.

Two uploaded files carried it, and both were found by looking at the pages:

* the Article File printed ``$w$ refutes, and does not select.`` -- a bold sentence
  opening with an inline formula;
* the tracked-changes file wraps **every** insertion in ``++...++``, so its whole new
  section reached the editor as raw Markdown.

A third defect lived in the diff builder: it stripped the ``$`` delimiters before the
word diff, which left the LaTeX body behind as ordinary words on the page.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


bmp = _load("bmp_markup", REPO / "tools" / "build_manuscript_pdf.py")
diff = _load("tracked_markup", REPO / "tools" / "build_tracked_changes.py")

DELIMITERS = ("$", "**", "~~", "++", "\\mathrm{")

#: Fixtures for the wrapping tests, with the column widths at which the *old* wrapper
#: breaks at the run boundary. The widths were measured, not chosen: at an arbitrary
#: width the line simply does not end there and the test passes with the defect put
#: back, which is what the first version of these tests did.
BOLD_FIXTURE = "x " * 40 + "computed for **39 of the 41**. The two exceptions"
BOLD_WIDTH = 150.1
MATH_FIXTURE = "y " * 40 + "no precise $w$; saying which tabulated row follows"
MATH_WIDTH = 386.5


def rendered(text: str) -> str:
    """The text a reader would see, with no math rasteriser available."""
    return "".join(run.text for run in bmp.parse_runs(text))


def test_math_inside_bold_is_not_printed_literally() -> None:
    out = rendered("**$w$ refutes, and does not select.** The four refuted events")
    assert "$" not in out, out
    assert "refutes, and does not select." in out


def test_markup_inside_an_insertion_is_resolved() -> None:
    out = rendered("++**The report carries a measurement.** DCM 113824 records $T$.++")
    for delimiter in DELIMITERS:
        assert delimiter not in out, f"{delimiter!r} survived: {out!r}"
    assert "The report carries a measurement." in out


def test_an_insertion_keeps_its_underline_through_the_nesting() -> None:
    runs = bmp.parse_runs("++**bold inserted text**++")
    assert runs, "nothing was produced"
    assert all(run.underline for run in runs), (
        "recursing into the insertion lost the revision marking, which is the only "
        "thing that tells a reader the text is new"
    )
    assert all(run.font == bmp.BOLD_FONT for run in runs)


def test_a_deletion_keeps_its_strike_through_the_nesting() -> None:
    runs = bmp.parse_runs("~~*removed emphasis*~~")
    assert runs and all(run.strike for run in runs)


def test_a_line_never_breaks_inside_a_word_at_a_run_boundary() -> None:
    """A run boundary is not a word boundary.

    The supplement printed a line ending ``39 of the 41`` and the next beginning
    ``. The two exceptions``: the full stop after a bold span was wrapped as though it
    were a word of its own. The source is ``**39 of the 41**.`` with no space.
    """
    runs = bmp.parse_runs(BOLD_FIXTURE)
    lines = bmp.wrap_runs(runs, BOLD_WIDTH)
    assert len(lines) > 1, "the fixture did not wrap, so it tests nothing"
    starts = ["".join(r.text for r in line).lstrip() for line in lines[1:]]
    for start in starts:
        assert start[:1] not in {".", ",", ";", ":", ")", "%"}, (
            f"a line begins with the punctuation that closes the previous word: "
            f"{start[:30]!r}"
        )


def test_the_wrapping_gate_is_not_vacuous() -> None:
    """The old wrapper, on the same fixture, must produce the defect.

    Wrapping each run separately is what shipped. Re-creating it here keeps the two
    tests above from passing merely because the fixture does not happen to break at the
    bold span.
    """
    import re

    from reportlab.pdfbase import pdfmetrics

    runs = bmp.parse_runs(BOLD_FIXTURE)
    width = BOLD_WIDTH
    lines: list[list] = [[]]
    x = 0.0
    for run in runs:  # the wrapper as it was
        for token in re.split(r"(\s+)", run.text):
            if not token:
                continue
            w = pdfmetrics.stringWidth(token, run.font, run.size)
            if token.isspace():
                if lines[-1]:
                    lines[-1].append(" ")
                    x += pdfmetrics.stringWidth(" ", run.font, run.size)
                continue
            if x + w > width and lines[-1]:
                lines.append([])
                x = 0.0
            lines[-1].append(token)
            x += w
    starts = ["".join(line).lstrip() for line in lines if line][1:]
    assert any(s.startswith(".") for s in starts), (
        "the fixture does not reproduce the defect under the old wrapper, so the "
        "tests above would pass with the defect put back"
    )


def test_an_inline_equation_keeps_the_punctuation_that_follows_it() -> None:
    runs = bmp.parse_runs(MATH_FIXTURE)
    lines = bmp.wrap_runs(runs, MATH_WIDTH)
    assert len(lines) > 1, "the fixture did not wrap, so it tests nothing"
    starts = ["".join(r.text for r in line).lstrip() for line in lines[1:]]
    assert all(not s.startswith(";") for s in starts), starts


def test_the_word_diff_keeps_math_whole() -> None:
    old = "the table speed gives $w = L\\,t_{rot}/(T\\,p)$ for the width"
    new = "the table speed gives $w = L\\,t_{rot}/(T\\,p)$ for the beam width"
    out = diff.word_diff(old, new)
    assert "$w = L\\,t_{rot}/(T\\,p)$" in out, (
        f"the math span did not survive the diff intact: {out!r}"
    )
    assert "\\mathrm" not in out
    assert "++beam++" in out or "++beam width++" in out


def test_the_word_diff_still_sees_a_changed_formula() -> None:
    """Protecting math must not make two different formulae compare equal."""
    out = diff.word_diff("the pitch is $p$ here", "the pitch is $q$ here")
    assert "~~$p$~~" in out and "++$q$++" in out, out


def test_an_unchanged_formula_is_not_marked_as_changed() -> None:
    out = diff.word_diff("before $p$ end", "before $p$ finish")
    assert "~~$p$~~" not in out and "++$p$++" not in out, out
