"""Prove the reproducibility gate by putting both defects back.

``tests/test_pdf_reproducible.py`` asserts that two builds of one source give the same
bytes. A gate that asserts something already true of the current code says nothing about
whether it would notice the thing going wrong, so each cause of the churn is restored
here, one at a time, and the gate has to fail on it:

1. ``invariant`` off, which is what the builder did until 2026-11-09: the build time goes
   into ``/CreationDate`` and ``/ModDate`` and the trailer ``/ID`` is random.
2. images named by filename instead of by their pixels, so the random
   ``TemporaryDirectory`` holding the rasterised equations renames every XObject.

The second injection only bites on a document that embeds an image, which is why the
fixture carries an equation and a figure; the harness checks that too, by refusing a
proof run in which no image was embedded.

Run::

    python tools/prove_pdf_reproducible.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "bmp", REPO / "tools" / "build_manuscript_pdf.py"
)
bmp = importlib.util.module_from_spec(spec)
sys.modules["bmp"] = bmp  # dataclasses resolves annotations through sys.modules
spec.loader.exec_module(bmp)

sys.path.insert(0, str(REPO / "tests"))
from test_pdf_reproducible import FIXTURE  # noqa: E402

FIGURES = REPO / "paper" / "figures"


def build(out: Path, *, invariant: int, name_images_by_path: bool) -> bytes:
    """Build the fixture, optionally with one of the two defects put back."""
    original_canvas = bmp.pdfcanvas.Canvas
    original_image = bmp._image

    class Canvas(original_canvas):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["invariant"] = invariant
            super().__init__(*a, **kw)

    bmp.pdfcanvas.Canvas = Canvas
    if name_images_by_path:
        bmp._image = lambda path: str(path)  # the defect: digest the filename
    try:
        with tempfile.TemporaryDirectory() as work:
            # cwd is the repository root, so resolve the fixture's relative figure path
            # the way the builder does when it runs on the manuscript.
            lines = bmp.build_lines(FIXTURE, Path(work), include_figures=True)
            bmp.draw(lines, out, "proof")
    finally:
        bmp.pdfcanvas.Canvas = original_canvas
        bmp._image = original_image
    return out.read_bytes()


def pair(tmp: Path, label: str, **defect) -> tuple[bytes, bytes]:
    first = build(tmp / f"{label}_1.pdf", **defect)
    second = build(tmp / f"{label}_2.pdf", **defect)
    return first, second


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def main() -> int:
    import os

    os.chdir(REPO / "paper")
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        clean = pair(tmp, "clean", invariant=1, name_images_by_path=False)
        n_images = clean[0].count(b"/FormXob.")
        print(f"the fixture embeds {n_images} image(s)")
        if n_images < 2:
            print(
                "REFUSED: the fixture embedded fewer than two images, so the "
                "image-naming injection cannot bite and this proof is vacuous"
            )
            return 2
        if digest(clean[0]) != digest(clean[1]):
            failures.append("the current builder is not reproducible")
        print(f"  current builder:            {digest(clean[0])} == {digest(clean[1])}"
              f"  {'ok' if clean[0] == clean[1] else 'DIFFERS'}")

        for label, defect, what in (
            ("timestamp", {"invariant": 0, "name_images_by_path": False},
             "the build time and a random /ID are written"),
            ("image-name", {"invariant": 1, "name_images_by_path": True},
             "images are named after their temporary filename"),
            ("both", {"invariant": 0, "name_images_by_path": True},
             "both defects at once"),
        ):
            a, b = pair(tmp, label, **defect)
            caught = a != b
            print(f"  injected {label:<11} ({what}): "
                  f"{digest(a)} vs {digest(b)}  {'caught' if caught else 'MISSED'}")
            if not caught:
                failures.append(f"the gate does not notice when {what}")

    if failures:
        print("\nREFUSED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall 3 injections caught; the current builder is byte-reproducible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
