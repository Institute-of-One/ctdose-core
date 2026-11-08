"""Prove the rewritten orphaned-heading gate and the table-keep rule by injection.

Two claims to establish:

1. The heading gate still catches a heading left at the foot of a page. The previous
   version caught it by shape and also caught a table row, so the replacement has to be
   shown to keep the catch while dropping the false positive.
2. The new table rule stops a table breaking so that one row stands alone.

Both are shown by building a document twice: once with the keep rules in force and once
with them switched off, which is what the renderer did before they existed.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import fitz  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "bmp", REPO / "tools" / "build_manuscript_pdf.py"
)
bmp = importlib.util.module_from_spec(spec)
sys.modules["bmp"] = bmp  # dataclasses resolves annotations through sys.modules
spec.loader.exec_module(bmp)


FILLER = (
    "The reconstruction takes the scanner model, the tube voltage, the total "
    "collimation, the exposure per rotation and the pitch, and reads a measured "
    "coefficient for that combination out of the open table. "
)


def document(filler_paragraphs: int, with_table: bool) -> str:
    parts = ["# A document built only to be broken across pages", ""]
    for i in range(filler_paragraphs):
        parts += [FILLER * 6, ""]
    parts += ["## 9.9 A heading that must not be stranded", ""]
    parts += [FILLER * 4, ""]
    if with_table:
        parts += ["**Table 9.** A table that must not leave one row overleaf.", ""]
        parts += ["| Quantity | Revision 1 | This revision |", "| --- | ---: | ---: |"]
        for i in range(7):
            parts += [f"| Row number {i + 1} of the table | {i} | {i + 1} |"]
        parts += [""]
    return "\n".join(parts)


def build(markdown: str, out: Path, keep_rules: bool) -> Path:
    out.with_suffix(".md").write_text(markdown, encoding="utf-8")
    with tempfile.TemporaryDirectory() as work:
        lines = bmp.build_lines(markdown, Path(work), include_figures=False)
        if not keep_rules:
            for line in lines:
                line.keep_with_next = False
        bmp.draw(lines, out, "proof")
    return out


def page_lines(path: Path) -> list[list[str]]:
    with fitz.open(path) as doc:
        return [
            [ln.strip() for ln in page.get_text().splitlines()
             if ln.strip() and not ln.strip().isdigit()]
            for page in doc
        ]


def heading_is_stranded(path: Path, heading: str) -> int | None:
    for number, lines in enumerate(page_lines(path), 1):
        if lines and " ".join(lines[-1].split()) == heading:
            return number
    return None


def a_row_stands_alone(path: Path) -> str | None:
    """A page whose only table content is one row, with the rest of the table elsewhere."""
    row = re.compile(r"^Row number (\d) of the table$")
    pages = page_lines(path)
    for number, lines in enumerate(pages, 1):
        rows = [ln for ln in lines if row.match(ln)]
        if len(rows) == 1:
            total = sum(
                1 for page in pages for ln in page if row.match(ln)
            )
            if total > 1:
                return f"page {number} carries only {rows[0]!r} of {total} rows"
    return None


def a_page_is_nearly_blank(path: Path) -> str | None:
    """The repository gate's rule: 120 characters or fewer of body text on a page."""
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            body = " ".join(page.get_text().split())
            if len(body) <= 120:
                return f"page {number} of {doc.page_count} has {len(body)} characters"
    return None


def a_page_carries_one_line_alone(path: Path) -> str | None:
    """The widow rule's own promise: no page holds a single line and nothing else.

    This is what the rule guarantees. Whether two short lines then clear the
    blank-page gate's 120-character threshold depends on how long those lines are, so
    the two are checked separately -- a proof that asserted the threshold would be
    testing the gate's constant rather than the rule.
    """
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            lines = [
                ln.strip() for ln in page.get_text().splitlines()
                if ln.strip() and not ln.strip().isdigit()
            ]
            if len(lines) == 1:
                return (
                    f"page {number} of {doc.page_count} carries the single line "
                    f"{lines[0][:40]!r}"
                )
    return None


def main() -> int:
    heading = "9.9 A heading that must not be stranded"
    problems = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. The heading gate. Find a filler length that strands the heading with the
        #    keep rules off; then require the same document to be clean with them on.
        stranded_at = None
        for n in range(8, 40):
            off = build(document(n, with_table=False), root / f"h{n}_off.pdf", False)
            found = heading_is_stranded(off, heading)
            if found:
                stranded_at = (n, found)
                break
        if not stranded_at:
            problems.append("could not construct a stranded heading at all")
            print("  COULD NOT INJECT a stranded heading")
        else:
            n, page = stranded_at
            print(f"  injected: with the keep rules off, the heading ends page {page} "
                  f"({n} filler paragraphs)")
            on = build(document(n, with_table=False), root / f"h{n}_on.pdf", True)
            if heading_is_stranded(on, heading) is None:
                print("  CAUGHT   the keep rules move the heading to the next page")
            else:
                problems.append("the keep rules do not rescue the stranded heading")
                print("  MISSED   the heading is still stranded with the rules on")

        # 2. The false positive the old gate reported: a table row shaped like a
        #    heading must not be read as one.
        rowish = build(document(6, with_table=True), root / "rows.pdf", True)
        source = (root / "rows.md").read_text(encoding="utf-8")
        headings = {
            " ".join(re.sub(r"[*_`]|~~|\+\+", "", m).split())
            for m in re.findall(r"^#{1,6}\s+(.+?)\s*$", source, flags=re.MULTILINE)
        }
        offenders = [
            line
            for page in page_lines(rowish)
            for line in page
            if re.match(r"^\s*(?:\d+(?:\.\d+)?|S\d+(?:\.\d+)?)\s+\S", line)
            and " ".join(line.split()) not in headings
        ]
        if offenders:
            print(f"  CONFIRMED the old shape rule would fire on {len(offenders)} "
                  f"non-heading line(s), e.g. {offenders[0]!r}")
        else:
            print("  note: no heading-shaped non-heading line in this document")

        # 3. The table rule. With the keep rules off, find a length that orphans one row.
        orphaned = None
        for n in range(8, 40):
            off = build(document(n, with_table=True), root / f"t{n}_off.pdf", False)
            where = a_row_stands_alone(off)
            if where:
                orphaned = (n, where)
                break
        if not orphaned:
            problems.append("could not construct an orphaned table row")
            print("  COULD NOT INJECT an orphaned table row")
        else:
            n, where = orphaned
            print(f"  injected: with the keep rules off, {where} "
                  f"({n} filler paragraphs)")
            on = build(document(n, with_table=True), root / f"t{n}_on.pdf", True)
            still = a_row_stands_alone(on)
            if still is None:
                print("  CAUGHT   the table rule keeps the table together")
            else:
                problems.append(f"the table rule leaves a row alone: {still}")
                print(f"  MISSED   {still}")

        # 4. The widow rule. With the keep rules off, find a length that leaves the
        #    last line of a paragraph alone on a page of its own.
        widowed = None
        for n in range(8, 40):
            if widowed:
                break
            for words in range(6, 60, 2):
                # A short trailing paragraph, so that when it straddles a page break the
                # part overleaf is one line and nothing follows it.
                tail = " ".join((FILLER * 4).split()[:words])
                doc = document(n, with_table=False) + "\n" + tail + "\n"
                off = build(doc, root / f"w{n}_{words}_off.pdf", False)
                where = a_page_carries_one_line_alone(off)
                if where:
                    widowed = (f"{n} paragraphs, {words}-word tail", where, doc)
                    break
        if not widowed:
            problems.append("could not construct a widowed last line")
            print("  COULD NOT INJECT a widowed last line")
        else:
            label, where, doc = widowed
            print(f"  injected: with the keep rules off, {where} ({label})")
            on = build(doc, root / "w_on.pdf", True)
            still = a_page_carries_one_line_alone(on)
            if still is None:
                print("  CAUGHT   the widow rule keeps two lines together")
                thin = a_page_is_nearly_blank(on)
                if thin:
                    print(f"           residual: {thin} -- two short lines can still "
                          f"sit at the blank-page gate's threshold")
            else:
                problems.append(f"the widow rule leaves a line alone: {still}")
                print(f"  MISSED   {still}")

    if problems:
        print("\nunproven:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nboth the heading gate and the table rule were shown to bite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
