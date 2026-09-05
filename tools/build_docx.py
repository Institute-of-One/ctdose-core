"""Render a simple Markdown document to .docx, with no converter installed.

The submission set carries Word copies of the title page and cover letter beside their
PDFs, because editorial offices ask for them. Neither pandoc nor python-docx is available
here, and a stale Word file is worse than none: the title page's article type changed to
Technical Note in this revision, and a .docx still reading "Research Article" would be
uploaded without anyone noticing the contradiction.

The documents this handles are plain -- paragraphs, headings, bold and italic runs -- so a
minimal Office Open XML package covers them exactly. It deliberately does *not* handle
tables, images or equations: the manuscript itself goes to the journal as a PDF built by
``build_manuscript_pdf.py``, and silently dropping a table from a .docx would be the same
class of failure this script exists to prevent, so it refuses instead.

Usage::

    python tools/build_docx.py paper/title_page_jacmp.md
    python tools/build_docx.py paper/cover_letter_jacmp_r1.md --out somewhere.docx
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parents[1]

#: A fixed timestamp, so rebuilding an unchanged source gives a byte-identical file.
FIXED_TIME = (2026, 1, 1, 0, 0, 0)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: Bold, italic and code spans, in the same order the PDF renderer resolves them.
INLINE = re.compile(r"(\*\*.+?\*\*|(?<!\*)\*[^*]+?\*(?!\*)|`[^`]+?`)")

HEADING_SIZE = {1: 32, 2: 26, 3: 24}  # half-points


def runs(text: str) -> str:
    out = []
    for piece in INLINE.split(text):
        if not piece:
            continue
        props = ""
        if piece.startswith("**") and piece.endswith("**"):
            piece, props = piece[2:-2], "<w:b/>"
        elif piece.startswith("*") and piece.endswith("*"):
            piece, props = piece[1:-1], "<w:i/>"
        elif piece.startswith("`") and piece.endswith("`"):
            piece, props = piece[1:-1], '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        # xml:space is required: Word strips leading and trailing spaces without it, which
        # would run adjacent styled spans together.
        out.append(
            f"<w:r><w:rPr>{props}</w:rPr>"
            f'<w:t xml:space="preserve">{escape(piece)}</w:t></w:r>'
        )
    return "".join(out)


def paragraph(lines: list[str], *, size: int | None = None) -> str:
    """One paragraph. Successive source lines become hard breaks, not a joined line.

    These documents are letterheads and title pages, where the address block is written
    one item per line and must stay that way; their prose paragraphs are each a single
    source line, so nothing is wrapped that should not be.
    """
    props = '<w:spacing w:after="160"/>'
    if size is not None:
        props += f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr>'
        body = (
            f'<w:r><w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(" ".join(lines))}</w:t></w:r>'
        )
    else:
        body = "<w:r><w:br/></w:r>".join(runs(line) for line in lines)
    return f"<w:p><w:pPr>{props}</w:pPr>{body}</w:p>"


def convert(markdown: str) -> str:
    """Markdown to the body of a WordprocessingML document."""
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraphs.append(paragraph(list(buffer)))
            buffer.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("|") or line.startswith("!["):
            raise SystemExit(
                "this converter handles prose only, and the source contains a table or an "
                f"image: {line[:60]!r}. Use the PDF build for documents with either."
            )
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            paragraphs.append(
                paragraph([line.lstrip("#").strip()], size=HEADING_SIZE.get(level, 24))
            )
            continue
        if set(line) <= {"-", "*", "_"} and len(line) >= 3:  # horizontal rule
            flush()
            paragraphs.append("<w:p/>")
            continue
        buffer.append(line)
    flush()

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(paragraphs)}'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr></w:body></w:document>"
    )


def write_docx(document_xml: str, out: Path) -> None:
    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "word/_rels/document.xml.rels": DOCUMENT_RELS,
        "word/document.xml": document_xml,
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts.items():
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    source = args.source.resolve()
    if not source.exists():
        raise SystemExit(f"source not found: {source}")
    out = (args.out or source.with_suffix(".docx")).resolve()

    document = convert(source.read_text(encoding="utf-8"))
    # Well-formedness is checked before the file is written: Word reports a corrupt
    # document with no indication of which part is wrong.
    from xml.etree import ElementTree

    ElementTree.fromstring(document)
    write_docx(document, out)
    print(f"wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
