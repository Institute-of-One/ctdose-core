"""Two builds of the same source must give the same bytes.

A ledger that records "the author opened this PDF and read it" is worth nothing if the
PDF gets new bytes every time it is built: the ledger goes permanently red, which says
nothing about what the reader saw, and a real content change cannot be told from noise.
``git status`` showed all six built PDFs modified after every run of ``build_all.py`` for
exactly that reason.

Two things were random. ReportLab writes the build time into ``/CreationDate`` and
``/ModDate`` and a random trailer ``/ID``, both suppressed by ``invariant=1``. And
``Canvas.drawImage`` names an embedded image's XObject after the *filename* when handed a
path -- the equations are rasterised into a ``TemporaryDirectory`` with a random name, so
every build produced different XObject names in a different order. Handed an
``ImageReader`` it digests the pixels instead.

The fixture here is deliberately small but it carries both kinds of image: a display
equation, which is rasterised to a temporary file, and a figure, which is read from the
repository. A text-only fixture would pass with the image defect put back.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILDER = REPO / "tools" / "build_manuscript_pdf.py"
FIGURE = REPO / "paper" / "figures" / "fig1_layer1_rdsr_agreement.png"

FIXTURE = """# Reproducibility fixture

A paragraph with an inline equation $D_w = 2\\sqrt{A/\\pi}$ and a reported value of
−18.2%, so the embedded TrueType face and the minus sign are both exercised.

$$\\mathrm{CTDI_{vol}} = \\frac{n\\mathrm{CTDI_w}}{100}\\cdot\\frac{I\\,t_{rot}}{p}$$

![Figure 1](figures/fig1_layer1_rdsr_agreement.png)

**Figure 1.** *A caption, so the italic face is used as well.*
"""


def build(source: Path, out: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(BUILDER), str(source), "--out", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.exists(), "the builder reported success and wrote nothing"


@pytest.fixture(scope="module")
def built_twice(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, bytes]:
    pytest.importorskip("reportlab")
    assert FIGURE.exists(), f"the fixture's figure is missing: {FIGURE}"
    tmp = tmp_path_factory.mktemp("reproducible")
    # Inside paper/ so the relative figure path in the fixture resolves exactly as the
    # manuscript's does.
    source = REPO / "paper" / "_reproducibility_fixture.md"
    source.write_text(FIXTURE, encoding="utf-8")
    try:
        first, second = tmp / "first.pdf", tmp / "second.pdf"
        build(source, first)
        build(source, second)
        return first.read_bytes(), second.read_bytes()
    finally:
        source.unlink(missing_ok=True)


def test_the_same_source_builds_the_same_bytes(built_twice: tuple[bytes, bytes]) -> None:
    first, second = built_twice
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest(), (
        "two builds of an unchanged source gave different bytes, so no ledger can pin "
        "the built PDF; compare the two files to find what varied"
    )


def test_the_fixture_actually_embeds_images(built_twice: tuple[bytes, bytes]) -> None:
    """Guard against the test passing because nothing was embedded."""
    first, _ = built_twice
    assert first.count(b"/FormXob.") >= 2, (
        "the fixture produced fewer than two embedded images, so it cannot detect an "
        "image named after a random temporary path"
    )


def test_no_build_timestamp_is_written(built_twice: tuple[bytes, bytes]) -> None:
    """``invariant`` replaces the build time with a fixed date; make sure it is on."""
    first, _ = built_twice
    # Measured, not guessed: ReportLab's invariant mode writes 2000-01-01T00:00:00Z.
    assert b"/CreationDate (D:20000101000000+00'00')" in first, (
        "the PDF carries a real build timestamp, so its bytes change every run"
    )
