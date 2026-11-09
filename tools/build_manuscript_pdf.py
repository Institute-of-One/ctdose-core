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
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

REPO = Path(__file__).resolve().parents[1]

# --- byte-reproducible output -------------------------------------------------------------
#
# Two rebuilds of an unchanged manuscript must give the same bytes, or a ledger recording
# that a human opened a particular PDF can never stay true, `git status` reports six
# modified files after every build, and a real content change cannot be told from noise.
# Two things made the output differ run to run:
#
# * the document timestamps and the trailer /ID, handled by ReportLab's ``invariant``;
# * the names of the embedded images. ``Canvas.drawImage`` names an XObject by digesting
#   the *filename* when given a path, and the mathtext equations are rasterised into a
#   ``TemporaryDirectory`` whose name is random -- so every build produced different
#   XObject names in a different order. Given an ``ImageReader`` it digests the pixel
#   data instead, which is exactly what we want it keyed on.
#
# ``tests/test_pdf_reproducible.py`` builds the same source twice and compares the bytes.


def _image(path: Path | str) -> ImageReader:
    """An ImageReader for ``path``, cached, so its XObject is named by its pixels."""
    key = str(path)
    reader = _IMAGE_CACHE.get(key)
    if reader is None:
        reader = _IMAGE_CACHE[key] = ImageReader(key)
    return reader


_IMAGE_CACHE: dict[str, ImageReader] = {}

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

# --- embedded fonts ---------------------------------------------------------------------
#
# The base-14 fonts (Times-Roman and friends) are *referenced*, never embedded: a PDF
# using them relies on the reader having them. Several publishers reject that, and it
# also means the file renders differently depending on the machine. We therefore embed a
# real serif face. Times New Roman is preferred because it is what the journal expects;
# DejaVu Serif is the fallback because it ships with matplotlib, which is already a
# dependency of the figure pipeline, so the build stays reproducible on any machine.

FONT_CANDIDATES: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "TimesNewRoman",
        {
            "": "C:/Windows/Fonts/times.ttf",
            "Bold": "C:/Windows/Fonts/timesbd.ttf",
            "Italic": "C:/Windows/Fonts/timesi.ttf",
            "BoldItalic": "C:/Windows/Fonts/timesbi.ttf",
        },
    ),
    (
        "DejaVuSerif",
        {
            "": "{mpl}/DejaVuSerif.ttf",
            "Bold": "{mpl}/DejaVuSerif-Bold.ttf",
            "Italic": "{mpl}/DejaVuSerif-Italic.ttf",
            "BoldItalic": "{mpl}/DejaVuSerif-BoldItalic.ttf",
        },
    ),
)

MONO_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("DejaVuSansMono", "{mpl}/DejaVuSansMono.ttf"),
    ("CourierNew", "C:/Windows/Fonts/cour.ttf"),
)


def _mpl_font_dir() -> str:
    try:
        import matplotlib

        return str(Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf")
    except Exception:  # pragma: no cover - matplotlib always present with the figures
        return ""


def register_fonts() -> str:
    """Embed a serif family, returning the name used. Falls back to the base-14 fonts.

    Rebinds the module-level font names so every caller picks up the embedded family
    without threading a parameter through the whole renderer.
    """
    global BODY_FONT, BOLD_FONT, ITALIC_FONT, MONO_FONT

    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase.ttfonts import TTFont

    mpl = _mpl_font_dir()
    for family, faces in FONT_CANDIDATES:
        paths = {k: Path(v.format(mpl=mpl)) for k, v in faces.items()}
        if not mpl and any("{mpl}" in v for v in faces.values()):
            continue
        if not all(p.is_file() for p in paths.values()):
            continue
        try:
            for suffix, path in paths.items():
                pdfmetrics.registerFont(TTFont(f"{family}{suffix}", str(path)))
        except Exception:  # pragma: no cover - a corrupt system font
            continue
        addMapping(family, 0, 0, family)
        addMapping(family, 1, 0, f"{family}Bold")
        addMapping(family, 0, 1, f"{family}Italic")
        addMapping(family, 1, 1, f"{family}BoldItalic")
        BODY_FONT, BOLD_FONT, ITALIC_FONT = family, f"{family}Bold", f"{family}Italic"

        for mono_name, mono_path in MONO_CANDIDATES:
            p = Path(mono_path.format(mpl=mpl))
            if p.is_file():
                try:
                    pdfmetrics.registerFont(TTFont(mono_name, str(p)))
                except Exception:  # pragma: no cover
                    continue
                MONO_FONT = mono_name
                break
        return family
    return "base-14 (not embedded)"


# Registered at import, before the dataclasses below capture the font names as their
# field defaults. Doing it lazily would leave every default bound to the base-14 names.
EMBEDDED_FAMILY = register_fonts()

HEADING_SIZES = {1: 15.0, 2: 12.5, 3: 11.5}


# --- glyph safety -----------------------------------------------------------------------
#
# The base-14 PDF fonts are limited to WinAnsi (cp1252). A character outside it does not
# fail loudly: ReportLab draws a black square, which is easy to miss in proof and
# embarrassing in submission. One shipped draft carried nine of them -- every negative
# percentage -- because a typographic minus sign is not a hyphen.
#
# Characters that have a faithful WinAnsi equivalent are substituted here. Anything else
# must be written as inline math in the source (``$\chi^2$``), which is rasterised with an
# embedded font; :func:`assert_encodable` fails the build rather than let it through.

SAFE_SUBSTITUTIONS = {
    "−": "-",   # MINUS SIGN -> hyphen-minus
    "‐": "-",   # HYPHEN
    "‑": "-",   # NON-BREAKING HYPHEN
    " ": " ",   # NO-BREAK SPACE
    " ": " ",   # THIN SPACE
    " ": " ",   # NARROW NO-BREAK SPACE
    "′": "'",   # PRIME
    "″": '"',   # DOUBLE PRIME
}


#: Characters worth keeping when the embedded font can actually draw them.
#:
#: Substituting U+2212 for a hyphen defeats the reason it is in the source. A hyphen is
#: a line-break opportunity, so "-59.4%" can be split across two lines as "-" and
#: "59.4%", which is how a reported negative number becomes a dangling dash in a
#: reviewer's copy. The substitution exists because the base-14 PDF fonts are limited to
#: WinAnsi, which has no U+2212 and would draw a black square. When a TrueType family is
#: embedded and the glyph is present in it, there is nothing to protect against, and the
#: character the source asked for should reach the page.
PRESERVE_IF_DRAWABLE = ("−",)


def _font_can_draw(char: str) -> bool:
    """Whether the active body font has a glyph for this character."""
    try:
        face = pdfmetrics.getFont(BODY_FONT).face
    except Exception:  # pragma: no cover - font not registered
        return False
    # A base-14 face has no TrueType character map and is WinAnsi-limited.
    cmap = getattr(face, "charToGlyph", None)
    return cmap is not None and ord(char) in cmap


def sanitise(text: str) -> str:
    for bad, good in SAFE_SUBSTITUTIONS.items():
        if bad in PRESERVE_IF_DRAWABLE and _font_can_draw(bad):
            continue
        text = text.replace(bad, good)
    return text


def unencodable(text: str) -> list[str]:
    """Characters that would render as a black square, after substitution.

    The test is what the *active* font can draw, not cp1252. With the base-14 fallback
    the two are the same, because those faces are WinAnsi-limited; with an embedded
    TrueType family the font's character map is wider, and holding it to cp1252 would
    reject characters it draws perfectly well -- U+2212 among them.
    """
    out = []
    for ch in sanitise(text):
        if _font_can_draw(ch):
            continue
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            out.append(ch)
    return out


def assert_encodable(markdown: str) -> None:
    """Refuse to build a PDF containing characters the body font cannot draw.

    Math and code spans are exempt: ``$...$`` is rasterised with mathtext, which embeds
    its own font, so Greek letters and superscripts belong there rather than in prose.
    """
    # Display math first: stripping inline math first leaves the `$$` delimiters behind,
    # and the odd `$` then pairs across paragraphs and hides real offenders. Inline math
    # is not allowed to span a blank line, so an unbalanced `$` can only damage one
    # paragraph rather than the rest of the document.
    prose = re.sub(r"\$\$.*?\$\$", " ", markdown, flags=re.DOTALL)
    prose = re.sub(r"\$[^$\n]+?\$", " ", prose)
    prose = re.sub(r"`[^`\n]+?`", " ", prose)
    offenders = sorted(set(unencodable(prose)))
    if offenders:
        import unicodedata

        detail = ", ".join(
            f"U+{ord(c):04X} ({unicodedata.name(c, '?')})" for c in offenders
        )
        raise SystemExit(
            "manuscript contains characters the PDF body font cannot render, which would "
            f"appear as black squares: {detail}.\n"
            "Write them as inline math -- $\\chi^2$, $\\geq$ -- so they are rasterised "
            "with an embedded font, or use an ASCII equivalent."
        )


@dataclass
class Run:
    """A styled span within one rendered line, or an inline image (an equation)."""

    text: str
    font: str = BODY_FONT
    size: float = BODY_SIZE
    image: Path | None = None
    image_w: float = 0.0
    image_h: float = 0.0
    #: Revision marking, for the tracked-changes build: struck-through text was removed
    #: and underlined text was added. Ordinary manuscripts never set either.
    strike: bool = False
    underline: bool = False

    def __post_init__(self) -> None:
        self.text = sanitise(self.text)

    def width(self) -> float:
        if self.image is not None:
            return self.image_w
        return pdfmetrics.stringWidth(self.text, self.font, self.size)


@dataclass
class Cell:
    """One table cell: wrapped lines of styled runs, plus its horizontal alignment."""

    lines: list[list[Run]] = field(default_factory=list)
    align: str = "left"


@dataclass
class TableRow:
    """One rendered table row. ``header`` rows are drawn bold with a rule beneath."""

    cells: list[Cell] = field(default_factory=list)
    header: bool = False
    height: float = 0.0


@dataclass
class Line:
    """One rendered line: styled runs, an image to place, or a table row."""

    runs: list[Run] = field(default_factory=list)
    space_before: float = 0.0
    numbered: bool = True
    image: Path | None = None
    image_w: float = 0.0
    image_h: float = 0.0
    align: str = "left"
    row: TableRow | None = None
    col_x: list[float] = field(default_factory=list)
    col_w: list[float] = field(default_factory=list)
    rule_below: bool = False
    rule_above: bool = False
    #: Keep this line on the same page as the one after it. Set by :func:`keep_groups`
    #: for table captions, headings and table header rows -- the places where a page
    #: break separates something from the thing it introduces.
    keep_with_next: bool = False


# --- inline markup ---------------------------------------------------------------------------

INLINE = re.compile(
    r"(~~.+?~~|\+\+.+?\+\+|\*\*.+?\*\*|(?<!\*)\*[^*]+?\*(?!\*)|`[^`]+?`|\$[^$]+?\$)",
    re.DOTALL,
)


def parse_runs(
    text: str,
    base: str = BODY_FONT,
    size: float = BODY_SIZE,
    *,
    math_dir: Path | None = None,
    math_counter: list[int] | None = None,
    underline: bool = False,
    strike: bool = False,
) -> list[Run]:
    """Split a paragraph into styled runs, resolving **bold**, *italic*, `code`, $math$.

    Inline ``$...$`` is rasterised with mathtext when ``math_dir`` is given, so formulae
    read as formulae rather than as stripped LaTeX; otherwise it degrades to italic text.

    **Nested spans are resolved by recursion.** ``INLINE`` is a flat alternation, so
    whichever delimiter opens first swallows everything to its close and the markup
    inside it used to be printed literally. Two uploaded files carried that:
    ``**$w$ refutes, and does not select.**`` printed as ``$w$ refutes`` in the Article
    File, and the whole of the tracked-changes file's inserted text -- every insertion
    is wrapped in ``++...++`` -- reached the editor as raw Markdown, asterisks, dollars,
    backslashes and all. Nothing that reads the source could see either one.
    """
    runs: list[Run] = []

    def nested(inner: str, font: str, **flags) -> list[Run]:
        return parse_runs(
            inner, font, size,
            math_dir=math_dir, math_counter=math_counter,
            underline=flags.get("underline", underline),
            strike=flags.get("strike", strike),
        )

    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("~~") and piece.endswith("~~"):
            runs.extend(nested(piece[2:-2], base, strike=True))
        elif piece.startswith("++") and piece.endswith("++"):
            runs.extend(nested(piece[2:-2], base, underline=True))
        elif piece.startswith("**") and piece.endswith("**"):
            runs.extend(nested(piece[2:-2], BOLD_FONT))
        elif piece.startswith("*") and piece.endswith("*"):
            runs.extend(nested(piece[1:-1], ITALIC_FONT))
        elif piece.startswith("`") and piece.endswith("`"):
            runs.append(
                Run(piece[1:-1], MONO_FONT, size - 1.0,
                    underline=underline, strike=strike)
            )
        elif piece.startswith("$") and piece.endswith("$"):
            run = _inline_math_run(piece[1:-1], size, math_dir, math_counter)
            run.underline, run.strike = underline, strike
            runs.append(run)
        else:
            runs.append(Run(piece, base, size, underline=underline, strike=strike))
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


def _fragment_width(fragment: Run) -> float:
    if fragment.image is not None:
        return fragment.image_w
    return pdfmetrics.stringWidth(fragment.text, fragment.font, fragment.size)


def _words(runs: list[Run]) -> list[list[Run]]:
    """Group the runs into words: a break is possible only at a space.

    A run boundary is not a word boundary. Wrapping run by run let a line break fall
    between a bold span and the punctuation that closes its sentence, and the supplement
    printed a line ending ``39 of the 41`` with the next beginning ``. The two
    exceptions``. The same break is possible after an inline equation -- ``w`` followed
    by ``;`` -- which is how a semicolon could have started a line.
    """
    words: list[list[Run]] = []
    pending: list[Run] = []
    for run in runs:
        if run.image is not None:  # an inline equation: part of the word it sits in
            pending.append(run)
            continue
        for token in re.split(r"(\s+)", run.text):
            if not token:
                continue
            fragment = Run(
                " " if token.isspace() else token,
                run.font, run.size, strike=run.strike, underline=run.underline,
            )
            if token.isspace():
                if pending:
                    words.append(pending)
                    pending = []
                words.append([fragment])
            else:
                pending.append(fragment)
    if pending:
        words.append(pending)
    return words


def _is_space(word: list[Run]) -> bool:
    return len(word) == 1 and word[0].image is None and word[0].text == " "


def wrap_runs(runs: list[Run], width: float) -> list[list[Run]]:
    """Greedy word wrap over styled runs, preserving each run's font."""
    lines: list[list[Run]] = [[]]
    x = 0.0
    for word in _words(runs):
        w = sum(_fragment_width(fragment) for fragment in word)
        if _is_space(word):
            if lines[-1]:
                lines[-1].extend(word)
                x += w
            continue
        if x + w > width and lines[-1]:
            while lines[-1] and lines[-1][-1].image is None and lines[-1][-1].text == " ":
                lines[-1].pop()
            lines.append([])
            x = 0.0
        lines[-1].extend(word)
        x += w
    return [ln for ln in lines if ln]


# --- tables ---------------------------------------------------------------------------------

TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
#: A GitHub-flavoured alignment row: ``| --- | ---: | :---: |``
TABLE_RULE = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")

#: Table text is set smaller than the body so a five-column table fits the measure.
TABLE_SIZE = 9.5
TABLE_LEADING = 1.35 * TABLE_SIZE
TABLE_PAD = 4.0


def split_row(raw: str) -> list[str]:
    return [c.strip() for c in raw.strip().strip("|").split("|")]


def column_alignments(rule: str, n: int) -> list[str]:
    """Read ``:---``/``---:``/``:---:`` into left/right/centre, defaulting to left."""
    out = []
    for spec in split_row(rule):
        left, right = spec.startswith(":"), spec.endswith(":")
        out.append("center" if left and right else "right" if right else "left")
    while len(out) < n:
        out.append("left")
    return out[:n]


def measure_columns(rows: list[list[str]], width: float) -> list[float]:
    """Allocate width by natural content width, shrinking only what overflows.

    A fixed equal split wastes space on ``Manufacturer`` and starves the wide
    ``Recoverable by either route``; a purely natural split overflows the measure. This
    gives every column what it needs when the total fits, and scales proportionally when
    it does not, with a floor so no column collapses to nothing.
    """
    n = max(len(r) for r in rows)
    natural = [0.0] * n
    for row in rows:
        for i, cell in enumerate(row):
            plain = re.sub(r"\*\*|`|\*", "", sanitise(cell))
            font = BOLD_FONT if row is rows[0] else BODY_FONT
            natural[i] = max(natural[i], pdfmetrics.stringWidth(plain, font, TABLE_SIZE))
    pad = 2 * TABLE_PAD
    total = sum(natural) + n * pad
    if total <= width:
        slack = (width - total) / n
        return [w + pad + slack for w in natural]
    floor = 0.09 * width
    scaled = [max(floor, (w + pad) * width / total) for w in natural]
    over = sum(scaled) - width
    if over > 0:  # trim the widest columns until it fits
        order = sorted(range(n), key=lambda i: -scaled[i])
        for i in order:
            take = min(over, scaled[i] - floor)
            scaled[i] -= take
            over -= take
            if over <= 1e-6:
                break
    return scaled


def build_table(
    raw_rows: list[str], runs_for, width: float
) -> list[Line]:
    """Turn a markdown pipe table into laid-out table rows.

    Rendered as a journal-style booktabs table: a rule above the header, one beneath it
    and one below the body, no vertical rules, numeric columns right-aligned.
    """
    header = split_row(raw_rows[0])
    aligns = column_alignments(raw_rows[1], len(header))
    body = [split_row(r) for r in raw_rows[2:]]
    cols = measure_columns([header, *body], width)

    xs, x = [], 0.0
    for w in cols:
        xs.append(x)
        x += w

    def make_row(cells: list[str], is_header: bool) -> Line:
        built = []
        for i, text in enumerate(cells):
            base = BOLD_FONT if is_header else BODY_FONT
            inner = cols[i] - 2 * TABLE_PAD
            wrapped = wrap_runs(runs_for(text, base, TABLE_SIZE), inner)
            # The header follows its column's alignment: a right-aligned number under a
            # left-aligned heading reads as belonging to the column to its left.
            built.append(Cell(lines=wrapped or [[Run("", base, TABLE_SIZE)]],
                              align=aligns[i]))
        height = max(len(c.lines) for c in built) * TABLE_LEADING + 2 * TABLE_PAD
        row = TableRow(cells=built, header=is_header, height=height)
        return Line(row=row, col_x=xs, col_w=cols, numbered=True)

    lines = [make_row(header, True)]
    lines[0].space_before = 8.0
    lines[0].rule_above = True
    lines[0].rule_below = True
    for cells in body:
        lines.append(make_row(cells, False))
    lines[-1].rule_below = True
    return lines


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

        # A pipe table: a header row, an alignment rule, then the body. Without this the
        # rows fall through to the paragraph branch and the pipes are typeset literally.
        if TABLE_ROW.match(raw) and i + 1 < len(blocks) and TABLE_RULE.match(blocks[i + 1]):
            table_rows = [blocks[i], blocks[i + 1]]
            i += 2
            while i < len(blocks) and TABLE_ROW.match(blocks[i]):
                table_rows.append(blocks[i])
                i += 1
            lines.extend(build_table(table_rows, runs_for, COLUMN_W))
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

        # Ordinary paragraph: join until a blank line, except where a line ends with
        # two spaces. That is Markdown's hard line break, and without it an address
        # block has to be written as one paragraph per line -- which is how the cover
        # letter came to print "Shuji Yamamoto Institute of One, LISIT Co., Ltd. Tokyo,
        # Japan yamamoto@lisit.jp ORCID: ..." as a single run-on line, and why spacing
        # it out instead pushed the signature onto a page of its own.
        segments: list[list[str]] = [[stripped]]
        hard_break = raw.rstrip("\r\n").endswith("  ")
        i += 1
        while i < len(blocks) and blocks[i].strip() and not re.match(
            r"^(#|>|\$\$|!\[|\d+\.\s|[-*]\s)", blocks[i].strip()
        ):
            if hard_break:
                segments.append([])
            segments[-1].append(blocks[i].strip())
            hard_break = blocks[i].rstrip("\r\n").endswith("  ")
            i += 1
        first = len(lines)
        for k, segment in enumerate(segments):
            para(" ".join(segment), space=6.0 if k == 0 else 0.0)
        if len(segments) > 1:
            # Lines the author joined with hard breaks are one block -- a letterhead,
            # an address, a signature -- and a page break inside one is the split the
            # SOP forbids. The cover letter put "Sincerely, Shuji Yamamoto" at the foot
            # of page 3 and the rest of the signature alone on page 4.
            for line in lines[first:-1]:
                line.keep_with_next = True

    mark_keep_groups(lines)
    return lines


#: The height one page can hold between the margins. A table taller than this cannot be
#: kept whole however the breaks fall, so it is kept together only when it fits.
USABLE_PAGE_H = PAGE_H - MARGIN_T - MARGIN_B


def _mark_table_rows(lines: list[Line]) -> None:
    """Keep each table whole where it fits, and never leave one row of it alone.

    Tables are contiguous runs of row lines in the emitted stream, so they are found by
    grouping rather than declared. The height used here is the same one the paginator
    measures with, so a table judged to fit really does fit.
    """
    index = 0
    while index < len(lines):
        if lines[index].row is None:
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].row is not None:
            index += 1
        table = lines[start:index]

        height = sum(line.row.height + line.space_before for line in table)
        if height <= USABLE_PAGE_H:
            # Short enough to fit a page: move the whole table rather than break it.
            for line in table[:-1]:
                line.keep_with_next = True
            table[-1].keep_with_next = False
            continue

        # Too tall to keep whole. Keep the header with the first data row, and the last
        # two rows with each other, so no page opens or closes on a single row.
        for line in table:
            line.keep_with_next = bool(line.row.header)
        if len(table) >= 2:
            table[-2].keep_with_next = True
            table[-1].keep_with_next = False


def _mark_paragraph_widows(lines: list[Line]) -> None:
    """Never let a page break leave one line of a paragraph by itself.

    A paragraph here is a run of body lines in which only the first carries
    ``space_before``; the wrapper produces them that way. Without this rule the last
    line of a paragraph can land alone on a page of its own: the tracked-changes
    document ended with a page holding the four words "by stratum, median marked.", the
    tail of the final figure legend, because that document drops its images and so the
    legend had nothing after it to keep with.

    Two lines of a paragraph therefore always travel together, which is the ordinary
    typographic rule for widows and orphans, and the blank-page gate checks the result.
    """
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.row is not None or line.image:
            index += 1
            continue
        start = index
        index += 1
        while (
            index < len(lines)
            and lines[index].row is None
            and not lines[index].image
            and lines[index].space_before == 0.0
        ):
            index += 1
        paragraph = lines[start:index]
        if len(paragraph) >= 2:
            # The penultimate line keeps with the last, so the break goes earlier.
            paragraph[-2].keep_with_next = True


def mark_keep_groups(lines: list[Line]) -> None:
    """Flag the lines that must not be separated from what follows them.

    Three cases, each one a way a page break puts a label on one page and the thing it
    labels on the next:

    * **a table caption and its table.** "Table 2." sat alone at the foot of a page with
      its table overleaf. The caption is a paragraph like any other to the renderer, so
      nothing connected the two.
    * **a heading and its first line of text.** The manuscript gates read the Markdown,
      where a page does not exist, so an orphaned heading is invisible to every check
      except looking at the printed page.
    * **a table's header row and its first data row**, for the same reason: a header row
      alone at a page foot repeats nothing useful.
    * **a table split so that one row stands alone overleaf.** A seven-row table in the
      point-by-point response broke after its sixth row, putting "Validation arms 2 3"
      at the top of the next page with nothing around it. A table short enough to fit a
      page is therefore kept whole, and a table too long for one page keeps its last two
      rows together, so a break never leaves a single row by itself.

    Marked here rather than at emission because it is a property of a line's neighbour,
    and the emitter writes each block without looking ahead.
    """
    def text_of(line: Line) -> str:
        return "".join(run.text for run in line.runs)

    _mark_table_rows(lines)
    _mark_paragraph_widows(lines)

    for index, line in enumerate(lines[:-1]):
        following = lines[index + 1]

        if line.row is not None:
            continue  # handled above, as a property of the whole table

        # A caption paragraph, where a table or image follows. The whole paragraph has
        # to keep with it, not only its final line: a caption wraps to three or four
        # lines, and marking just the last one lets the first three break away and sit
        # at the foot of the previous page.
        if following.row is not None or following.image:
            line.keep_with_next = True
            k = index
            while (
                k > 0
                and lines[k].space_before == 0.0
                and lines[k].row is None
                and not lines[k].image
            ):
                k -= 1
                lines[k].keep_with_next = True
            continue

        # A heading keeps with the line after it.
        if line.runs and all(
            run.font in (BOLD_FONT,) for run in line.runs if run.text.strip()
        ):
            size = max((run.size for run in line.runs), default=BODY_SIZE)
            if size > BODY_SIZE:
                line.keep_with_next = True


# --- pagination and drawing ----------------------------------------------------------------------


def draw(lines: list[Line], out_path: Path, title: str) -> Path:
    # initialFontName matters even though every draw call sets its own font: ReportLab
    # writes the canvas default into each page's initial graphics state, so leaving it at
    # Helvetica lists an unembedded font on every page of an otherwise fully embedded PDF.
    c = pdfcanvas.Canvas(
        str(out_path),
        pagesize=LETTER,
        initialFontName=BODY_FONT,
        initialFontSize=BODY_SIZE,
        invariant=1,  # fixed timestamps and trailer /ID; see _image() above
    )
    c.setTitle(title)
    y = PAGE_H - MARGIN_T
    lineno = 0
    page = 1

    def footer() -> None:
        c.setFont(BODY_FONT, 9)
        c.setFillGray(0.45)
        c.drawCentredString(PAGE_W / 2, MARGIN_B - 26, f"{page}")
        c.setFillGray(0)

    def draw_rule(y_at: float, weight: float = 0.9) -> None:
        c.setLineWidth(weight)
        c.setStrokeGray(0.25)
        c.line(MARGIN_L, y_at, MARGIN_L + COLUMN_W, y_at)
        c.setStrokeGray(0)

    def draw_revision_mark(run: Run, x_at: float, baseline: float) -> None:
        """Strike a deletion through the middle, underline an insertion below it."""
        if not (run.strike or run.underline):
            return
        offset = 0.30 * run.size if run.strike else -0.22 * run.size
        c.setLineWidth(0.5)
        c.line(x_at, baseline + offset, x_at + run.width(), baseline + offset)

    def height_of(line: Line) -> float:
        if line.row is not None:
            return line.row.height + line.space_before
        if line.image:
            return line.image_h + line.space_before
        return LEADING + line.space_before

    for index, line in enumerate(lines):
        # A line marked keep_with_next must have room for itself and for everything it
        # is keeping with, or the break goes before it instead of after it. Without
        # this, "Table 2." sat alone at the foot of a page with its table overleaf.
        needed = height_of(line)
        j = index
        while lines[j].keep_with_next and j + 1 < len(lines):
            j += 1
            needed += height_of(lines[j])
        if y - needed < MARGIN_B:
            footer()
            c.showPage()
            page += 1
            y = PAGE_H - MARGIN_T
        y -= line.space_before

        if line.row is not None:
            row = line.row
            if line.rule_above:
                draw_rule(y + 2, 1.1)
            # The line number sits against the row's first text baseline, so a table row
            # numbers once however many lines its cells wrap to.
            lineno += 1
            c.setFont(BODY_FONT, 8)
            c.setFillGray(0.5)
            c.drawRightString(LINENO_X, y - TABLE_SIZE - TABLE_PAD, str(lineno))
            c.setFillGray(0)

            for idx, cell in enumerate(row.cells):
                cx, cw = MARGIN_L + line.col_x[idx], line.col_w[idx]
                inner = cw - 2 * TABLE_PAD
                cy = y - TABLE_PAD
                for run_line in cell.lines:
                    cy -= TABLE_LEADING
                    text_w = sum(r.width() for r in run_line)
                    if cell.align == "right":
                        tx = cx + TABLE_PAD + inner - text_w
                    elif cell.align == "center":
                        tx = cx + TABLE_PAD + (inner - text_w) / 2
                    else:
                        tx = cx + TABLE_PAD
                    for run in run_line:
                        if run.image is not None:
                            c.drawImage(_image(run.image), tx, cy - 0.28 * run.image_h,
                                        width=run.image_w, height=run.image_h, mask="auto")
                        else:
                            c.setFont(run.font, run.size)
                            c.drawString(tx, cy, run.text)
                            draw_revision_mark(run, tx, cy)
                        tx += run.width()
            y -= row.height
            if line.rule_below:
                draw_rule(y + 2)
            continue

        if line.image is not None:
            x = MARGIN_L + (COLUMN_W - line.image_w) / 2 if line.align == "center" else MARGIN_L
            c.drawImage(_image(line.image), x, y - line.image_h, width=line.image_w,
                        height=line.image_h, mask="auto")
            y -= line.image_h + 4
            continue

        lineno += 1
        c.setFont(BODY_FONT, 8)
        c.setFillGray(0.5)
        c.drawRightString(LINENO_X, y - BODY_SIZE, str(lineno))
        c.setFillGray(0)

        x = MARGIN_L
        baseline = y - BODY_SIZE
        for run in line.runs:
            if run.image is not None:
                # mathtext rasters carry a descender margin; drop the image slightly so
                # its baseline lines up with the surrounding text.
                c.drawImage(_image(run.image), x, baseline - 0.28 * run.image_h,
                            width=run.image_w, height=run.image_h, mask="auto")
            else:
                c.setFont(run.font, run.size)
                c.drawString(x, baseline, run.text)
                draw_revision_mark(run, x, baseline)
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
    assert_encodable(markdown)
    with tempfile.TemporaryDirectory() as tmp:
        lines = build_lines(markdown, Path(tmp), include_figures=not args.no_figures)
        title = markdown.splitlines()[0].lstrip("# ").strip()
        draw(lines, out, title)

    n_numbered = sum(1 for ln in lines if ln.image is None)
    n_tables = sum(1 for ln in lines if ln.row is not None and ln.row.header)
    print(
        f"wrote {out}  ({n_numbered} numbered lines, {n_tables} table(s), "
        f"font {EMBEDDED_FAMILY})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
