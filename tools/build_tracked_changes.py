"""Derive the tracked-changes document for a revision, as a script rather than by hand.

The journal's revision screen has an "Article Tracked Changes File" slot, and the honest
artefact for it is a diff of what was actually submitted last time against what is being
submitted now -- not a prose summary of what the author believes changed. This builds
that diff mechanically, so it cannot drift from the two manuscripts it compares.

Both inputs are the *masked* manuscripts, because the tracked-changes file is
review-facing and JACMP reviews double-masked. The output is verified against the same
forbidden-token list the masked build uses.

The diff is paragraph-aligned rather than a flat word diff. A flat diff over a 60%
condensation aligns unrelated sentences across section boundaries and reads as noise;
aligning blocks first keeps every heading, table and figure legend where a reader expects
it, and only paragraphs that genuinely correspond are compared word by word.

Markup, rendered by ``tools/build_manuscript_pdf.py``:

* ``~~text~~`` -- removed, struck through;
* ``++text++`` -- added, underlined.

Usage::

    python tools/build_tracked_changes.py
    python tools/build_tracked_changes.py --old A.md --new B.md --out-md C.md
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: The baseline is **what was uploaded last time**, not the newest thing that happens to
#: be on disk. For revision 1 that was the masked Research Article. For revision 2 it is
#: the masked Technical Note as submitted in revision 1, which is kept as its own file
#: because rebuilding the masked manuscript overwrites it -- and a diff against the
#: rebuilt file would be a diff of a document against itself, which is empty and passes.
#: Each round's submitted masked manuscript is frozen here as it goes out.
SUBMITTED = {
    "r1": REPO / "paper" / "ctdose-core_jacmp_masked.md",
    "r2": REPO / "paper" / "ctdose-core_jacmp_technote_masked_r1.md",
}
CURRENT_ROUND = "r2"

OLD = SUBMITTED[CURRENT_ROUND]
NEW = REPO / "paper" / "ctdose-core_jacmp_technote_masked.md"
OUT_MD = REPO / "paper" / "ctdose-core_jacmp_technote_tracked_changes.md"
OUT_PDF = REPO / "paper" / "ctdose-core_jacmp_technote_tracked_changes.pdf"

#: A block is compared word by word with its counterpart only if they are this similar.
#: Below it the two are treated as an unrelated deletion and insertion, which is the
#: right reading for a paragraph that was rewritten rather than edited.
PAIR_THRESHOLD = 0.45

HEADER = """# Tracked changes — Revision 1

**Manuscript:** An open-source, provenance-aware framework for reconstructing missing CT
dose indices from DICOM metadata. **Article type:** Technical Note (changed from Research
Article at the Section Editor's request).

This file compares the previously submitted masked manuscript with the masked Technical
Note submitted now. ~~Struck-through text~~ was removed from the main text; ++underlined
text++ is new. Text with no marking is unchanged.

The condensation is a relocation, not a re-analysis: no number changed, and the material
removed here is reproduced verbatim in the Supplementary Material file and in the public
repository. Both documents are anonymous for double-masked review.

---

"""


def blocks(markdown: str) -> list[str]:
    """Split a manuscript into comparable blocks: paragraphs, headings, table rows.

    Blank-line separated, except that a display equation opened with ``$$`` is kept whole
    however many lines it spans -- splitting one would diff its two halves independently.
    """
    out: list[str] = []
    buf: list[str] = []
    in_equation = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if in_equation:
            buf.append(line)
            if line.endswith("$$"):
                in_equation = False
                out.append(" ".join(buf))
                buf = []
            continue
        if line.startswith("$$"):
            if buf:
                out.append(" ".join(buf))
                buf = []
            closed_on_this_line = line.endswith("$$") and len(line) > 4
            buf.append(line)
            if closed_on_this_line:
                out.append(" ".join(buf))
                buf = []
            else:
                in_equation = True
            continue
        if not line.strip():
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        # A heading, a table row or an image is its own block even without a blank line
        # around it, so a changed table row does not drag its neighbours into the diff.
        if re.match(r"^\s*(#|\||!\[|---\s*$)", line):
            if buf:
                out.append(" ".join(buf))
                buf = []
            out.append(line.strip())
            continue
        buf.append(line.strip())
    if buf:
        out.append(" ".join(buf))
    return [b for b in out if b]


def plain(text: str) -> str:
    """Drop emphasis and math delimiters before a word-level diff.

    A revision mark can fall between a ``**`` and its partner, leaving markup the
    renderer then pairs across the mark and typesets wrongly. The tracked-changes file is
    a diff rather than a typeset manuscript, so the delimiters are simply removed and
    formulae read as their source; the manuscript itself keeps both.
    """
    return re.sub(r"\*\*|(?<!\w)\*(?!\w)|\$", "", text)


def word_diff(old: str, new: str) -> str:
    """Mark up one block against its counterpart, word by word."""
    a, b = plain(old).split(), plain(new).split()
    pieces: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            pieces.append(" ".join(a[i1:i2]))
            continue
        if tag in ("delete", "replace"):
            pieces.append("~~" + " ".join(a[i1:i2]) + "~~")
        if tag in ("insert", "replace"):
            pieces.append("++" + " ".join(b[j1:j2]) + "++")
    return " ".join(p for p in pieces if p)


def similarity(old: str, new: str) -> float:
    """How far two blocks are the same paragraph, compared as word sequences.

    Character-bag similarity (``quick_ratio``) is order-blind, and on this manuscript it
    scored the abstract's long Conclusion against the new Methods above its own much
    shorter counterpart. Matching word sequences is slower but pairs the paragraphs a
    reader would pair.
    """
    matcher = difflib.SequenceMatcher(None, plain(old).split(), plain(new).split(),
                                      autojunk=False)
    if matcher.real_quick_ratio() <= PAIR_THRESHOLD:
        return 0.0
    return matcher.ratio()


def is_structural(block: str) -> bool:
    """Headings, rules, images and table rows are moved, never word-diffed."""
    return bool(re.match(r"^(#|\||!\[|---\s*$|\$\$)", block))


def mark(block: str, marker: str) -> str:
    """Wrap a whole block, leaving structural blocks unmarked but annotated.

    A struck-through heading would still be typeset as a heading and read as if the
    section survived, so removals and additions of structure are named in words instead.
    """
    if not block.strip():
        return block
    if is_structural(block):
        label = "removed" if marker == "~~" else "added"
        if block.startswith("#"):
            level = len(block) - len(block.lstrip("#"))
            return f"{'#' * level} [{label}] {block.lstrip('#').strip()}"
        if block.startswith("!["):
            return f"++[figure {label}: {block}]++" if label == "added" else \
                   f"~~[figure removed: {block}]~~"
        return f"{marker}{block}{marker}"
    return f"{marker}{block}{marker}"


def align(old: list[str], new: list[str]) -> list[str]:
    """Produce the marked-up document from two block lists."""
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(old[i1:i2])
            continue
        removed, added = list(old[i1:i2]), list(new[j1:j2])
        # Pair blocks best-match-first over the whole changed region rather than in
        # document order. Taking each new block's best remaining partner in turn lets an
        # early paragraph claim a later one's true counterpart -- which put the abstract's
        # Conclusion against its Methods on the first attempt.
        candidates = sorted(
            (
                (similarity(o, n), k, m)
                for k, o in enumerate(removed) if not is_structural(o)
                for m, n in enumerate(added) if not is_structural(n)
            ),
            key=lambda t: -t[0],
        )
        paired: dict[int, int] = {}   # new block index -> old block index
        claimed: set[int] = set()
        for ratio, k, m in candidates:
            if ratio <= PAIR_THRESHOLD:
                break
            if k in claimed or m in paired:
                continue
            claimed.add(k)
            paired[m] = k

        for m, block in enumerate(added):
            if m in paired:
                out.append(word_diff(removed[paired[m]], block))
            else:
                out.append(mark(block, "++"))
        for k, block in enumerate(removed):
            if k not in claimed:
                out.append(mark(block, "~~"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", type=Path, default=OLD)
    ap.add_argument("--new", type=Path, default=NEW)
    ap.add_argument("--out-md", type=Path, default=OUT_MD)
    ap.add_argument("--out-pdf", type=Path, default=OUT_PDF)
    args = ap.parse_args()
    old, new, out_md, out_pdf = (
        p.resolve() for p in (args.old, args.new, args.out_md, args.out_pdf)
    )

    for path in (old, new):
        if not path.exists():
            raise SystemExit(f"missing input: {path}. Build the masked manuscripts first.")

    marked = align(
        blocks(old.read_text(encoding="utf-8")),
        blocks(new.read_text(encoding="utf-8")),
    )
    document = HEADER + "\n\n".join(marked) + "\n"
    out_md.write_text(document, encoding="utf-8", newline="")
    print(f"wrote {out_md.relative_to(REPO)}  ({len(document.split())} words)")

    build = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_manuscript_pdf.py"),
         str(out_md), "--out", str(out_pdf), "--no-figures"],
        cwd=REPO, capture_output=True, text=True,
    )
    sys.stdout.write(build.stdout)
    if build.returncode != 0:
        sys.stderr.write(build.stderr)
        return build.returncode

    # The tracked-changes file is review-facing, so it is held to the masked build's
    # standard rather than trusted because its inputs were masked.
    sys.path.insert(0, str(REPO / "tools"))
    from build_masked_manuscript import verify

    leaked = verify(out_pdf)
    if leaked:
        print("\nMASKING FAILED in the tracked-changes PDF:")
        for token in leaked:
            print(f"  - {token}")
        return 1
    print(f"\nmasking verified: {out_pdf.name} identifies nobody")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
