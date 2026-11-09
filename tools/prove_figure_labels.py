"""Prove the figure-label checker by putting both defects back into the real figure.

The two defects are the ones found by opening Figure 3 and looking at it:

1. the per-stratum summaries placed at the right-hand edge of the data, where the widest
   errors reach +87%, so markers were printed over the digits -- ``n=40`` with a marker
   through the ``4``;
2. the band note appended to the axis label, which then ran off the right edge of the
   canvas, printing ``±20%`` as ``±20``.

Each is restored by patching the drawing call, and **the real figure is then drawn, with
the real data at the real size**. A synthetic stand-in was tried first and was worse than
useless: on a one-panel fixture ``tight_layout`` absorbs an over-long label so the
clipping cannot happen at all, and on a two-panel one the marker and text sizes no longer
line up, so the proof reported misses that the checker was not responsible for. If the
figure under test is not the figure that is submitted, the proof is about something else.

Run::

    python tools/prove_figure_labels.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cfl", REPO / "tools" / "check_figure_labels.py"
)
cfl = importlib.util.module_from_spec(spec)
sys.modules["cfl"] = cfl
spec.loader.exec_module(cfl)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.axes  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

FIGURE = "fig3_fallback_error.png"


def draw(mf, survey, *, labels_at_the_right_edge: bool, band_in_the_axis_label: bool):
    """Draw the real Figure 3, optionally with one of the defects restored."""
    captured: list = []

    real_annotate = matplotlib.axes.Axes.annotate
    real_set_xlabel = matplotlib.axes.Axes.set_xlabel
    real_save = mf._save

    def annotate(self, text, xy=None, **kw):
        if labels_at_the_right_edge and isinstance(text, str) and text.startswith("n="):
            kw = {**kw, "ha": "right", "va": "center"}
            xy = (155, round(xy[1]))  # back beside the row, inside the data area
        return real_annotate(self, text, xy=xy, **kw)

    def set_xlabel(self, label, *a, **kw):
        if band_in_the_axis_label and str(label).startswith("relative error"):
            label = f"{label}; shaded band ±20%"
        return real_set_xlabel(self, label, *a, **kw)

    def capture(fig, name: str):
        captured.append((fig, name))
        return None  # nothing is written to disk by this proof

    matplotlib.axes.Axes.annotate = annotate
    matplotlib.axes.Axes.set_xlabel = set_xlabel
    mf._save = capture
    try:
        mf.figure_fallback_error(survey)
    finally:
        matplotlib.axes.Axes.annotate = real_annotate
        matplotlib.axes.Axes.set_xlabel = real_set_xlabel
        mf._save = real_save

    assert captured, "the figure function did not produce a figure"
    fig, name = captured[-1]
    assert name == FIGURE, f"expected {FIGURE}, got {name}"
    try:
        return cfl.inspect(fig, name)
    finally:
        plt.close(fig)


def main() -> int:
    mf = cfl.load_make_figures()
    survey = mf._load("survey.json")

    clean = draw(mf, survey, labels_at_the_right_edge=False,
                 band_in_the_axis_label=False)
    print(f"the layout in use: {len(clean)} problem(s)")
    for p in clean:
        print(f"  - {p}")
    if clean:
        print("REFUSED: the layout in use does not pass, so nothing below means anything")
        return 2

    missed: list[str] = []
    for what, defect, expect in (
        (
            "summaries at the right edge of the data",
            {"labels_at_the_right_edge": True, "band_in_the_axis_label": False},
            "drawn over the label",
        ),
        (
            "the band note appended to the axis label",
            {"labels_at_the_right_edge": False, "band_in_the_axis_label": True},
            "runs off the canvas",
        ),
        (
            "both at once",
            {"labels_at_the_right_edge": True, "band_in_the_axis_label": True},
            None,
        ),
    ):
        found = draw(mf, survey, **defect)
        caught = bool(found) and (expect is None or any(expect in p for p in found))
        print(f"  {'caught ' if caught else 'MISSED '} {what}")
        for p in found[:2]:
            print(f"            -> {p}")
        if not caught:
            missed.append(what)

    if missed:
        print(f"\nREFUSED: {len(missed)} injection(s) not caught: {missed}")
        return 1
    print("\nall 3 injections caught; the layout in use passes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
