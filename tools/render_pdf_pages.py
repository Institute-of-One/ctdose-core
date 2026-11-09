"""Render every page of every uploaded PDF to a PNG, so the pages can be looked at.

The runbook's pre-submission check asks for the pages to be *seen*: a heading alone at
the foot of a page, a caption separated from its table, a figure label stamped over a
panel, a colour the legend does not name. None of that is visible to a gate that reads
Markdown, and several of those defects reached reviewers in this programme.

The page images are also what a visual-review ledger can pin. The PDF itself is now
byte-reproducible (``tests/test_pdf_reproducible.py``), and the page images are
reproducible with it, so a recorded review stays true across a rebuild that changes
nothing and goes stale the moment the content changes.

Written to ``paper/build/pages/<stem>/page01.png`` ... and listed in
``paper/build/pages/index.json`` with each page's sha256.

Usage::

    python tools/render_pdf_pages.py
    python tools/render_pdf_pages.py --dpi 200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import fitz  # PyMuPDF

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
OUT = PAPER / "build" / "pages"

#: The files that go to the journal, plus the two letters that are read by the editor.
#: Keep in step with ``tests/test_pdf_layout.py``'s UPLOADED and the submission kit's
#: file table; ``tests/test_visual_review.py`` checks that this list covers every PDF the
#: kit says to upload.
RENDERED: tuple[str, ...] = (
    "ctdose-core_jacmp_technote_masked.pdf",
    "ctdose-core_jacmp_technote_tracked_changes.pdf",
    "ctdose-core_jacmp_technote_supplement.pdf",
    "response_to_editor_jacmp_r2.pdf",
    "title_page_jacmp.pdf",
    "cover_letter_jacmp_r2.pdf",
)

DEFAULT_DPI = 150


def render(pdf: Path, dpi: int) -> list[tuple[str, str]]:
    """Render ``pdf`` to one PNG per page; return (relative path, sha256) per page."""
    folder = OUT / pdf.stem
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("page*.png"):
        old.unlink()
    pages: list[tuple[str, str]] = []
    with fitz.open(pdf) as doc:
        for number, page in enumerate(doc, 1):
            image = folder / f"page{number:02d}.png"
            page.get_pixmap(dpi=dpi).save(image)
            data = image.read_bytes()
            pages.append(
                (image.relative_to(PAPER).as_posix(), hashlib.sha256(data).hexdigest())
            )
    return pages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    args = ap.parse_args(argv)

    missing = [name for name in RENDERED if not (PAPER / name).exists()]
    if missing:
        print("not built yet; run python paper/build_all.py first:")
        for name in missing:
            print(f"  - paper/{name}")
        return 1

    index: dict[str, object] = {
        "_why": (
            "Every page of every uploaded PDF, as an image, because the defects this "
            "programme keeps shipping are the ones only visible on the page. The "
            "sha256 of each page is what paper/visual_review.json pins."
        ),
        "dpi": args.dpi,
        "documents": {},
    }
    total = 0
    for name in RENDERED:
        pdf = PAPER / name
        pages = render(pdf, args.dpi)
        total += len(pages)
        index["documents"][name] = {
            # The artefact these images came from, so a review can be tied to the
            # document that was looked at and not merely to the images.
            "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "pages": [
                {"page": i, "image": rel, "sha256": sha}
                for i, (rel, sha) in enumerate(pages, 1)
            ],
        }
        print(f"{name}: {len(pages)} page(s)")

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n{total} page image(s) at {args.dpi} dpi under {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
