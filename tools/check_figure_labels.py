"""Look for labels printed on top of data, and for labels printed off the page.

Found by opening the figures: in Figure 3B the per-stratum annotations sat at the right
edge of the data area, where the widest errors reach +87%, so two of them were printed
with a data point through the digits -- ``n=40`` with a marker over the ``4``. The first
fix moved the band note into the axis label, which then ran off the right edge of the
figure. Both are invisible to every other gate here: the numbers were right, the result
files were right, and the figure was regenerated faithfully.

So two questions are asked of every figure, at the moment it is drawn:

1. **Is any text drawn over a data marker?** Compared as rectangles in display space,
   with a small tolerance so that a marker merely touching a letter's bounding box --
   which is generously sized -- is not reported. A rule that fires on things that are
   not defects is a rule that gets ignored.
2. **Is any text outside the figure?** ``bbox_inches="tight"`` grows the canvas to fit
   what sticks out of the axes, but not past the figure's own right edge once the
   layout is fixed, so a long axis label is simply cut.

Run::

    python tools/check_figure_labels.py

``tests/test_figure_labels.py`` runs the same check. ``tools/prove_figure_labels.py``
puts both defects back and requires that they are caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]

#: How far a marker may reach into a text's bounding box before it is reported, in
#: display pixels. Text bounding boxes include the font's ascent and descent, so a
#: marker level with a lower-case word often overlaps the box while touching no glyph.
OVERLAP_TOLERANCE_PX = 3.0


def load_make_figures():
    spec = importlib.util.spec_from_file_location(
        "make_figures", REPO / "paper" / "figures" / "make_figures.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_figures"] = module
    spec.loader.exec_module(module)
    return module


def _shrink(bbox, pad: float):
    from matplotlib.transforms import Bbox

    return Bbox.from_extents(
        bbox.x0 + pad, bbox.y0 + pad, bbox.x1 - pad, bbox.y1 - pad
    )


def labels_of(fig) -> list[str]:
    """Every string drawn on ``fig``: titles, axis labels, ticks, legends, annotations.

    A figure has two sources, the data and the code that draws it, and a word the
    manuscript has dropped can survive in the second one indefinitely: the legend of
    Figure 3A still said "headline stratum" after the revision had renamed it the
    primary stratum, and in IORN-005 a legend printed "channelised" after the body was
    converted. No gate that reads the result files can see it.
    """
    out: list[str] = []
    for ax in fig.axes:
        out.extend(t.get_text() for t in ax.texts)
        out.extend(
            t.get_text() for t in (ax.title, ax.xaxis.label, ax.yaxis.label)
        )
        out.extend(t.get_text() for t in ax.get_xticklabels())
        out.extend(t.get_text() for t in ax.get_yticklabels())
        legend = ax.get_legend()
        if legend is not None:
            out.extend(t.get_text() for t in legend.get_texts())
    out.extend(t.get_text() for t in fig.texts)
    return [s for s in out if s and s.strip()]


def inspect(fig, name: str) -> list[str]:
    """Every label of ``fig`` that overlaps a marker or falls outside the canvas."""
    from matplotlib.collections import PathCollection

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    canvas = fig.bbox
    problems: list[str] = []

    for ax in fig.axes:
        texts = [t for t in ax.texts if t.get_text().strip()]
        labels = [ax.xaxis.label, ax.yaxis.label, ax.title]
        for text in texts + [t for t in labels if t.get_text().strip()]:
            try:
                box = text.get_window_extent(renderer=renderer)
            except Exception:  # an artist with no extent yet
                continue
            if not canvas.containsx(box.x0) or not canvas.containsx(box.x1):
                problems.append(
                    f"{name}: {text.get_text()!r} runs off the canvas horizontally "
                    f"({box.x0:.0f}..{box.x1:.0f} against 0..{canvas.x1:.0f})"
                )
            if not canvas.containsy(box.y0) or not canvas.containsy(box.y1):
                problems.append(
                    f"{name}: {text.get_text()!r} runs off the canvas vertically "
                    f"({box.y0:.0f}..{box.y1:.0f} against 0..{canvas.y1:.0f})"
                )

        # Markers, as rectangles. Only scatter collections: a line, a span or an image
        # is background that text is routinely and deliberately placed over.
        markers = []
        for collection in ax.collections:
            if not isinstance(collection, PathCollection):
                continue
            offsets = collection.get_offsets()
            if offsets is None or len(offsets) == 0:
                continue
            sizes = collection.get_sizes()
            points = ax.transData.transform(offsets)
            for (x, y), size in zip(
                points, sizes if len(sizes) else [20.0] * len(points), strict=False
            ):
                radius = (float(size) ** 0.5) / 2.0 * fig.dpi / 72.0
                markers.append((x, y, radius))

        for text in texts:
            if not text.get_text().strip():
                continue
            try:
                box = _shrink(text.get_window_extent(renderer=renderer),
                              OVERLAP_TOLERANCE_PX)
            except Exception:
                continue
            hit = [
                (x, y)
                for x, y, r in markers
                if box.x0 < x + r and x - r < box.x1
                and box.y0 < y + r and y - r < box.y1
            ]
            if hit:
                problems.append(
                    f"{name}: {len(hit)} data marker(s) are drawn over the label "
                    f"{text.get_text()!r}"
                )
    return problems


#: The figures this submission ships. Figures 4-6 belong to the organ layer withdrawn at
#: revision 1; they are checked and reported, but they do not fail the gate, because a
#: defect in a figure nobody will see is not a reason to hold a submission.
SUBMITTED = (
    "fig1_layer1_rdsr_agreement.png",
    "fig2_recorded_dose_availability.png",
    "fig3_fallback_error.png",
)

#: Drawn somewhere this checker cannot reach. fig4 is produced by
#: ``ctdose_core``'s own ``save_modulation_figure`` rather than through ``_save`` here,
#: so it is not inspected. Named rather than passed over in silence.
NOT_INSPECTED = ("fig4_modulation_and_wed.png",)


def main() -> int:
    mf = load_make_figures()
    found: list[str] = []
    drawn: list[str] = []
    original_save = mf._save

    def checking_save(fig, name: str):
        drawn.append(name)
        found.extend(inspect(fig, name))
        return original_save(fig, name)

    mf._save = checking_save
    code = mf.main()
    if code != 0:
        print(f"the figure build failed with {code}")
        return code

    if not drawn:
        print("REFUSED: no figure was drawn, so nothing was checked")
        return 2
    missing = [name for name in SUBMITTED if name not in drawn]
    if missing:
        print(f"REFUSED: a submitted figure was never drawn, so it was never checked: "
              f"{missing}")
        return 2

    print(f"\nchecked {len(drawn)} figure(s): {', '.join(drawn)}")
    print(f"not inspected (drawn elsewhere): {', '.join(NOT_INSPECTED)}")

    blocking = [p for p in found if p.split(":")[0] in SUBMITTED]
    other = [p for p in found if p not in blocking]
    if other:
        print(f"\n{len(other)} problem(s) in figures this submission does not use:")
        for problem in other:
            print(f"  - {problem}")
    if blocking:
        print(f"\n{len(blocking)} problem(s) in a submitted figure:")
        for problem in blocking:
            print(f"  - {problem}")
        return 1
    print("\nno label in a submitted figure is drawn over a data marker, "
          "and none runs off the canvas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
