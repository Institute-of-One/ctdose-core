"""No label in a submitted figure may be printed over the data, or off the canvas.

Both defects shipped in Figure 3 of this revision and were found only by opening the
figure: the per-stratum summaries sat at the right-hand edge of the data area, where the
widest errors reach +87%, so two of them were printed with a marker through the digits;
the first fix moved the band note into the axis label, which then ran off the edge of the
canvas. Every other gate was green for both -- the numbers were right, the result files
were right, and the figure was faithfully regenerated from them.

The figures are drawn here and inspected in memory; nothing is written, so running the
suite cannot quietly replace an artefact somebody has reviewed.
``tools/prove_figure_labels.py`` puts both defects back into this same figure and
requires that they are caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "cfl", REPO / "tools" / "check_figure_labels.py"
)
cfl = importlib.util.module_from_spec(spec)
sys.modules["cfl"] = cfl
spec.loader.exec_module(cfl)


@pytest.fixture(scope="module")
def mf():
    pytest.importorskip("matplotlib")
    if not (REPO / "results" / "survey.json").is_file():
        pytest.skip("the result files are not in this checkout")
    return cfl.load_make_figures()


def drawn(mf, which: str):
    """Draw one figure and return (name, problems), writing nothing to disk."""
    import matplotlib.pyplot as plt

    captured: list = []
    real_save = mf._save
    mf._save = lambda fig, name: captured.append((fig, name))
    try:
        if which == "fig1":
            mf.figure_layer1(mf._load("rdsr_layer1.json"))
        elif which == "fig2":
            mf.figure_availability(mf._load("survey.json"))
        else:
            mf.figure_fallback_error(mf._load("survey.json"))
    finally:
        mf._save = real_save
    assert captured, f"{which} produced no figure"
    fig, name = captured[-1]
    try:
        return name, cfl.inspect(fig, name)
    finally:
        plt.close(fig)


@pytest.mark.parametrize("which", ["fig1", "fig2", "fig3"])
def test_no_label_is_drawn_over_the_data_or_off_the_canvas(mf, which: str) -> None:
    name, problems = drawn(mf, which)
    assert name in cfl.SUBMITTED, f"{name} is not in the submitted list"
    assert not problems, "\n".join(problems)


#: Words a figure must not print because the manuscript no longer uses them. Each was
#: withdrawn or corrected at this revision, and a figure is the one place a withdrawn
#: word survives every gate that reads text.
WITHDRAWN_IN_FIGURES = (
    "headline",            # renamed: the primary stratum
    "conservative",        # the withdrawn underestimate claim
    "measured value",      # the coefficient table is tabulated, not measured
    "normalised", "analysed", "centre", "favour",  # British forms; the journal is US
)


@pytest.mark.parametrize("which", ["fig1", "fig2", "fig3"])
def test_no_figure_prints_a_withdrawn_word(mf, which: str) -> None:
    """Read the labels the figure actually draws, not the code that might draw them."""
    import matplotlib.pyplot as plt

    captured: list = []
    real_save = mf._save
    mf._save = lambda fig, name: captured.append((fig, name))
    try:
        if which == "fig1":
            mf.figure_layer1(mf._load("rdsr_layer1.json"))
        elif which == "fig2":
            mf.figure_availability(mf._load("survey.json"))
        else:
            mf.figure_fallback_error(mf._load("survey.json"))
    finally:
        mf._save = real_save
    fig, name = captured[-1]
    try:
        labels = cfl.labels_of(fig)
    finally:
        plt.close(fig)

    assert labels, f"{name} drew no labels at all, so this checks nothing"
    found = [
        (word, label)
        for label in labels
        for word in WITHDRAWN_IN_FIGURES
        if word in label.lower()
    ]
    assert not found, f"{name} prints a word the manuscript has dropped: {found}"


def test_the_withdrawn_word_gate_reads_the_label_that_carried_one(mf) -> None:
    """Non-vacuity: the legend that said "headline stratum" is in what is scanned.

    Otherwise the test above passes because nothing it reads could ever contain one of
    these words.
    """
    import matplotlib.pyplot as plt

    captured: list = []
    real_save = mf._save
    mf._save = lambda fig, name: captured.append((fig, name))
    try:
        mf.figure_fallback_error(mf._load("survey.json"))
    finally:
        mf._save = real_save
    fig, _ = captured[-1]
    try:
        labels = [label.lower() for label in cfl.labels_of(fig)]
    finally:
        plt.close(fig)
    assert any("primary stratum" in label for label in labels), (
        f"the legend that carried the withdrawn word is not among the labels read: "
        f"{labels}"
    )


def test_every_submitted_figure_is_covered(mf) -> None:
    """Three figures are submitted and three are checked above."""
    names = {drawn(mf, which)[0] for which in ("fig1", "fig2", "fig3")}
    assert names == set(cfl.SUBMITTED), (
        f"the submitted figures are {cfl.SUBMITTED} but these were checked: {names}"
    )
