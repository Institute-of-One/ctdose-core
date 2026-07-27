"""Render the manuscript Markdown to a line-numbered, double-spaced PDF for submission.

Journals ask for continuous line numbers so reviewers can cite a location, and for
double spacing. Neither is available from a plain Markdown viewer, and no LaTeX or
pandoc installation is assumed here, so the layout is done directly with ReportLab: text
is wrapped to the measured column width, every rendered line is numbered in the margin,
and display equations are rasterised with matplotlib's mathtext so they appear as
equations rather than as raw LaTeX.

Usage::

    python tools/build_manuscript_pdf.py
    python tools/build_manuscript_pdf.py paper/ctdose-core_jacmp.md --out paper/manuscript.pdf
    python tools/build_manuscript_pdf.py --no-figures      # text only, faster
"""

from __future__ import annotations

import argparse
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

REPO = Path(__file__).resolve().parents[1]

# --- page geometry -----------------------------------------------------------------------
PAGE_W, PAGE_H = LETTER
MARGIN_L, MARGIN_R = 1.15 * inch, 0.95 * inch
MARGIN_T, MARGIN_B = 1.0 * inch, 0.9 * inch
COLUMN_W = PAGE_W - MARGIN_L - MARGIN_R
LINENO_X = MARGIN_L - 0.42 * inch

BODY_FONT, BODY_SIZE = "Times-Roman", 11.0
BOLD_FONT, ITALIC_FONT = "Times-Bold", "Times-Italic"
MONO_FONT, MONO_SIZE = "Courier", 9.5
LEADING = 2.0 * BODY_SIZE  # double-spaced

HEADING_SIZES = {1: 15.0, 2: 12.5, 3: 11.5}


@dataclass
class Run:
    """A styled span within one rendered line, or an inline image (an equation)."""

    text: str
    font: str = BODY_FONT
    size: float = BODY_SIZE
    image: Path | None = None
    image_w: float = 0.0
    image_h: float = 0.0

    def width(self) -> float:
        if self.image is not None:
            return self.image_w
        return pdfmetrics.stringWidth(self.text, self.font, self.size)


@dataclass
class Line:
    """One rendered line: either styled runs, or an image to place."""

    runs: list[Run] = field(default_factory=list)
    space_before: float = 0.0
    numbered: bool = True
    image: Path | None = None
    image_w: float = 0.0
    image_h: float = 0.0
    align: str = "left"


# --- inline markup ---------------------------------------------------------------------------

INLINE = re.compile(r"(\*\*.+?\*\*|(?<!\*)\*[^*]+?\*(?!\*)|`[^`]+?`|\$[^$]+?\$)", re.DOTALL)


def parse_runs(
    text: str,
    base: str = BODY_FONT,
    size: float = BODY_SIZE,
    *,
    math_dir: Path | None = None,
    math_counter: list[int] | None = None,
) -> list[Run]:
    """Split a paragraph into styled runs, resolving **bold**, *italic*, `code`, $math$.

    Inline ``$...$`` is rasterised with mathtext when ``math_dir`` is given, so formulae
    read as formulae rather than as stripped LaTeX; otherwise it degrades to italic text.
    """
    runs: list[Run] = []
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            runs.append(Run(piece[2:-2], BOLD_FONT, size))
        elif piece.startswith("*") and piece.endswith("*"):
            runs.append(Run(piece[1:-1], ITALIC_FONT, size))
        elif piece.startswith("`") and piece.endswith("`"):
            runs.append(Run(piece[1:-1], MONO_FONT, size - 1.0))
        elif piece.startswith("$") and piece.endswith("$"):
            run = _inline_math_run(piece[1:-1], size, math_dir, math_counter)
            runs.append(run)
        else:
            runs.append(Run(piece, base, size))
    return runs


def _inline_math_run(
    body: str, size: float, math_dir: Path | None, math_counter: list[int] | None
) -> Run:
    if math_dir is not None and math_counter is not None:
        math_counter[0] += 1
        rendered = render_equation(body, math_dir, math_counter[0], inline=True, target_pt=size)
        if rendered is not None:
            path, w, h = rendered
            return Run("", ITALIC_FONT, size, image=path, image_w=w, image_h=h)
    plain = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", body)
    plain = plain.replace("\\", "").replace("{", "").replace("}", "")
    return Run(plain, ITALIC_FONT, size)


def wrap_runs(runs: list[Run], width: float) -> list[list[Run]]:
    """Greedy word wrap over styled runs, preserving each run's font."""
    lines: list[list[Run]] = [[]]
    x = 0.0
    for run in runs:
        if run.image is not None:  # an inline equation is one unbreakable token
            if x + run.image_w > width and lines[-1]:
                while lines[-1] and lines[-1][-1].text == " ":
                    lines[-1].pop()
                lines.append([])
                x = 0.0
            lines[-1].append(run)
            x += run.image_w
            continue
        for token in re.split(r"(\s+)", run.text):
            if not token:
                continue
            w = pdfmetrics.stringWidth(token, run.font, run.size)
            if token.isspace():
                if lines[-1]:
                    lines[-1].append(Run(" ", run.font, run.size))
                    x += pdfmetrics.stringWidth(" ", run.font, run.size)
                continue
            if x + w > width and lines[-1]:
                while lines[-1] and lines[-1][-1].text == " ":
                    lines[-1].pop()
                lines.append([])
                x = 0.0
            lines[-1].append(Run(token, run.font, run.size))
            x += w
    return [ln for ln in lines if ln]


# --- equations ------------------------------------------------------------------------------


def render_equation(
    latex: str,
    out_dir: Path,
    index: int,
    *,
    inline: bool = False,
    target_pt: float = BODY_SIZE,
) -> tuple[Path, float, float] | None:
    """Rasterise an equation with matplotlib mathtext. Returns ``(path, w_pt, h_pt)``.

    ``inline`` scales the result to sit on a body-text line rather than as a display block.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import mathtext
        from matplotlib.font_manager import FontProperties
    except ImportError:  # pragma: no cover - optional at build time
        return None

    body = latex.strip().strip("$").strip()
    # mathtext understands a useful subset of LaTeX. Normalise the constructs it does
    # not: display spacing, \mathrm, and the \left. ... \middle/ ... \right. idiom.
    body = re.sub(r"\s+", " ", body)
    body = body.replace("\\qquad", " \\ \\ ").replace("\\,", " ")
    body = body.replace("\\left.", "").replace("\\right.", "").replace("\\middle/", "/")
    body = re.sub(r"\\mathrm\{([^{}]*)\}", r"\\mathregular{\1}", body)

    prefix = "ieq" if inline else "eq"
    path = out_dir / f"{prefix}_{index:03d}.png"
    prop = FontProperties(size=11 if inline else 13)
    try:
        mathtext.math_to_image(f"${body}$", str(path), prop=prop, dpi=300, format="png")
    except Exception:
        return None  # fall back to text rendering of the source

    from reportlab.lib.utils import ImageReader

    iw, ih = ImageReader(str(path)).getSize()
    if inline:
        # The raster was drawn at the body point size, so 300 dpi -> 72 pt/inch places it
        # at its natural size. Scaling to a fixed height instead would make a single
        # glyph with no ascender or descender come out far too large.
        scale = 72.0 / 300.0
        w, h = iw * scale, ih * scale
        if w > COLUMN_W * 0.9:
            return None
        return path, w, h

    scale = 0.235  # 300 dpi -> points, then a little smaller than the body text
    w, h = iw * scale, ih * scale
    if w > COLUMN_W:  # scale an over-wide equation down to the column
        h *= COLUMN_W / w
        w = COLUMN_W
    if h > 2.2 * inch:  # a runaway parse; better to show the source than a broken image
        return None
    return path, w, h


# --- markdown -> lines --------------------------------------------------------------------------


def build_lines(markdown: str, work_dir: Path, *, include_figures: bool = True) -> list[Line]:
    lines: list[Line] = []
    eq_index = 0
    inline_counter = [0]
    blocks = markdown.split("\n")
    i = 0

    def runs_for(text: str, base: str = BODY_FONT, size: float = BODY_SIZE) -> list[Run]:
        return parse_runs(text, base, size, math_dir=work_dir, math_counter=inline_counter)

    def para(text: str, base: str = BODY_FONT, size: float = BODY_SIZE, space: float = 6.0) -> None:
        for k, run_line in enumerate(wrap_runs(runs_for(text, base, size), COLUMN_W)):
            lines.append(Line(runs=run_line, space_before=space if k == 0 else 0.0))

    while i < len(blocks):
        raw = blocks[i]
        stripped = raw.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("$$"):  # display equation
            if stripped.endswith("$$") and len(stripped) > 4:
                buf = [stripped[2:-2]]  # opened and closed on one line
                i += 1
            else:
                buf = [stripped[2:]]
                i += 1
                while i < len(blocks) and "$$" not in blocks[i]:
                    buf.append(blocks[i])
                    i += 1
                if i < len(blocks):
                    buf.append(blocks[i].replace("$$", ""))
                    i += 1
            eq_index += 1
            rendered = render_equation("\n".join(buf), work_dir, eq_index)
            if rendered:
                path, w, h = rendered
                lines.append(Line(image=path, image_w=w, image_h=h, space_before=8.0,
                                  align="center", numbered=True))
            else:
                para(" ".join(b.strip() for b in buf), MONO_FONT, MONO_SIZE - 0.5)
            continue

        if stripped.startswith("!["):  # figure image
            m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", stripped)
            if m and include_figures:
                path = (REPO / "paper" / m.group(1)).resolve()
                if path.exists():
                    from reportlab.lib.utils import ImageReader

                    iw, ih = ImageReader(str(path)).getSize()
                    w = min(COLUMN_W, iw * 0.24)
                    lines.append(Line(image=path, image_w=w, image_h=ih * (w / iw),
                                      space_before=10.0, align="center"))
            i += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            size = HEADING_SIZES.get(level, 11.5)
            for k, run_line in enumerate(wrap_runs(runs_for(text, BOLD_FONT, size), COLUMN_W)):
                lines.append(Line(runs=run_line, space_before=(14.0 if k == 0 else 0.0)))
            i += 1
            continue

        if set(stripped) <= {"-", "*", "_"} and len(stripped) >= 3:  # horizontal rule
            lines.append(Line(runs=[Run("", BODY_FONT, BODY_SIZE)], space_before=8.0))
            i += 1
            continue

        if stripped.startswith(">"):  # block quote
            para(stripped.lstrip("> ").strip(), ITALIC_FONT, BODY_SIZE, space=6.0)
            i += 1
            continue

        list_match = re.match(r"^(\d+\.|[-*])\s+(.*)$", stripped)
        if list_match:
            marker, body = list_match.group(1), list_match.group(2)
            for k, run_line in enumerate(wrap_runs(runs_for(f"{marker} {body}"), COLUMN_W - 14)):
                lines.append(Line(runs=run_line, space_before=3.0 if k == 0 else 0.0))
            i += 1
            continue

        # Ordinary paragraph: join until a blank line.
        buf = [stripped]
        i += 1
        while i < len(blocks) and blocks[i].strip() and not re.match(
            r"^(#|>|\$\$|!\[|\d+\.\s|[-*]\s)", blocks[i].strip()
        ):
            buf.append(blocks[i].strip())
            i += 1
        para(" ".join(buf))

    return lines


# --- pagination and drawing ----------------------------------------------------------------------


def draw(lines: list[Line], out_path: Path, title: str) -> Path:
    c = pdfcanvas.Canvas(str(out_path), pagesize=LETTER)
    c.setTitle(title)
    y = PAGE_H - MARGIN_T
    lineno = 0
    page = 1

    def footer() -> None:
        c.setFont("Times-Roman", 9)
        c.setFillGray(0.45)
        c.drawCentredString(PAGE_W / 2, MARGIN_B - 26, f"{page}")
        c.setFillGray(0)

    for line in lines:
        needed = (line.image_h if line.image else LEADING) + line.space_before
        if y - needed < MARGIN_B:
            footer()
            c.showPage()
            page += 1
            y = PAGE_H - MARGIN_T
        y -= line.space_before

        if line.image is not None:
            x = MARGIN_L + (COLUMN_W - line.image_w) / 2 if line.align == "center" else MARGIN_L
            c.drawImage(str(line.image), x, y - line.image_h, width=line.image_w,
                        height=line.image_h, mask="auto")
            y -= line.image_h + 4
            continue

        lineno += 1
        c.setFont("Times-Roman", 8)
        c.setFillGray(0.5)
        c.drawRightString(LINENO_X, y - BODY_SIZE, str(lineno))
        c.setFillGray(0)

        x = MARGIN_L
        baseline = y - BODY_SIZE
        for run in line.runs:
            if run.image is not None:
                # mathtext rasters carry a descender margin; drop the image slightly so
                # its baseline lines up with the surrounding text.
                c.drawImage(str(run.image), x, baseline - 0.28 * run.image_h,
                            width=run.image_w, height=run.image_h, mask="auto")
            else:
                c.setFont(run.font, run.size)
                c.drawString(x, baseline, run.text)
            x += run.width()
        y -= LEADING

    footer()
    c.save()
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", nargs="?", type=Path,
                    default=REPO / "paper" / "ctdose-core_jacmp.md")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-figures", action="store_true",
                    help="omit embedded figure images (text and equations only)")
    args = ap.parse_args()

    if not args.source.exists():
        raise SystemExit(f"manuscript not found: {args.source}")
    out = args.out or args.source.with_suffix(".pdf")

    markdown = args.source.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        lines = build_lines(markdown, Path(tmp), include_figures=not args.no_figures)
        title = markdown.splitlines()[0].lstrip("# ").strip()
        draw(lines, out, title)

    n_numbered = sum(1 for ln in lines if ln.image is None)
    print(f"wrote {out}  ({n_numbered} numbered lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
