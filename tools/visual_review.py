"""The visual-review ledger: who looked at which pages, and whether that is still true.

Every other gate in this repository reads text. The defects that have actually reached
reviewers in this programme were on the page: a heading alone at the foot of a page, a
table caption one page from its table, a figure carrying another figure's number, a
colour the legend does not name, a title page whose last line spilled overleaf. The only
way to make "somebody looked" checkable is to record what they looked at.

What is pinned is the **page images**, not the PDF's own bytes -- in this repository the
PDF is byte-reproducible as well (``tests/test_pdf_reproducible.py``), but a ledger
pinning a document that gets a fresh timestamp on every build goes permanently red, and
a gate that can never be green says nothing about what the reader saw. The images are
also the thing a person actually sees.

A review is **current** when every page image of that document still hashes to what was
recorded. Rebuilding an unchanged manuscript keeps a review current; changing a word in
it makes exactly the affected document stale, and the check says which pages moved.

Two roles are recorded separately, because they are not the same evidence:

``agent``
    Claude read the rendered pages in the conversation. This catches the mechanical
    defects above and is what the test suite requires.
``author``
    The submitting author opened the pages himself. This is what the runbook requires
    before the files go to the journal, and no script can stand in for it.

Usage::

    python tools/visual_review.py --check                 # is every document reviewed?
    python tools/visual_review.py --check --role author   # ... by the author (submission)
    python tools/visual_review.py --record --by "..." --role agent --note "..."
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
LEDGER = PAPER / "visual_review.json"
INDEX = PAPER / "build" / "pages" / "index.json"

ROLES = ("agent", "author")

WHY = (
    "Who has looked at the rendered pages of each uploaded PDF, and the sha256 of the "
    "page images they looked at. Written by tools/visual_review.py --record; checked by "
    "tools/visual_review.py --check and tests/test_visual_review.py. Pins the page "
    "images rather than the PDF so that a rebuild of unchanged content keeps a review "
    "current, and a content change makes exactly the affected document stale."
)


def load_index() -> dict:
    if not INDEX.exists():
        raise SystemExit(
            "no page images yet. Run:\n"
            "  python paper/build_all.py\n"
            "  python tools/render_pdf_pages.py"
        )
    return json.loads(INDEX.read_text(encoding="utf-8"))


def load_ledger() -> dict:
    if not LEDGER.exists():
        return {"_why": WHY, "reviews": []}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def current_pages(index: dict, name: str) -> list[str]:
    return [page["sha256"] for page in index["documents"][name]["pages"]]


def status(index: dict | None = None, ledger: dict | None = None) -> dict[str, dict]:
    """Per document: the current review by each role, or why there is none."""
    index = index if index is not None else load_index()
    ledger = ledger if ledger is not None else load_ledger()
    out: dict[str, dict] = {}
    for name in index["documents"]:
        now = current_pages(index, name)
        entry: dict[str, object] = {"pages": len(now), "by_role": {}}
        for role in ROLES:
            seen = [
                review
                for review in ledger["reviews"]
                if review.get("role") == role and name in review.get("documents", {})
            ]
            if not seen:
                entry["by_role"][role] = {"state": "never reviewed"}
                continue
            # The most recent review by this role is the one that counts; an earlier one
            # cannot vouch for pages that have changed since.
            last = seen[-1]
            recorded = last["documents"][name]["page_sha256"]
            if recorded == now:
                entry["by_role"][role] = {
                    "state": "current",
                    "by": last.get("by"),
                    "at": last.get("at"),
                }
            else:
                moved = [
                    i + 1
                    for i in range(max(len(recorded), len(now)))
                    if recorded[i : i + 1] != now[i : i + 1]
                ]
                entry["by_role"][role] = {
                    "state": "stale",
                    "by": last.get("by"),
                    "at": last.get("at"),
                    "pages_changed": moved,
                    "pages_then": len(recorded),
                }
        out[name] = entry
    return out


def check(role: str) -> int:
    report = status()
    bad: list[str] = []
    for name, entry in sorted(report.items()):
        state = entry["by_role"][role]
        mark = {"current": "ok", "stale": "STALE", "never reviewed": "MISSING"}[
            state["state"]
        ]
        detail = ""
        if state["state"] == "current":
            detail = f"by {state['by']} on {state['at']}"
        elif state["state"] == "stale":
            pages = ", ".join(str(p) for p in state["pages_changed"][:8])
            more = " ..." if len(state["pages_changed"]) > 8 else ""
            detail = (
                f"reviewed by {state['by']} on {state['at']}; "
                f"page(s) {pages}{more} have changed since"
            )
        print(f"  {mark:<8} {name:<48} {entry['pages']:>2} page(s)  {detail}")
        if state["state"] != "current":
            bad.append(name)
    if bad:
        print(
            f"\n{len(bad)} document(s) without a current {role} review. "
            f"Look at the pages under paper/build/pages/<stem>/ and then record it:\n"
            f'  python tools/visual_review.py --record --by "<name>" --role {role} '
            f'--note "<what was checked>"'
        )
        return 1
    print(f"\nevery document carries a current {role} review")
    return 0


def record(by: str, role: str, note: str, documents: list[str] | None) -> int:
    index = load_index()
    names = documents or list(index["documents"])
    unknown = [n for n in names if n not in index["documents"]]
    if unknown:
        raise SystemExit(f"not rendered: {', '.join(unknown)}")

    ledger = load_ledger()
    ledger["_why"] = WHY
    ledger["reviews"].append(
        {
            "by": by,
            "role": role,
            "at": dt.date.today().isoformat(),
            "note": note,
            "dpi": index["dpi"],
            "documents": {
                name: {
                    "pdf_sha256": index["documents"][name]["pdf_sha256"],
                    "page_sha256": current_pages(index, name),
                }
                for name in names
            },
        }
    )
    LEDGER.write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    pages = sum(len(current_pages(index, n)) for n in names)
    print(
        f"recorded a {role} review by {by} of {len(names)} document(s), "
        f"{pages} page(s), in {LEDGER.relative_to(REPO)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report, change nothing")
    ap.add_argument("--record", action="store_true", help="record a review")
    ap.add_argument("--role", choices=ROLES, default="agent")
    ap.add_argument("--by", help="who looked (required with --record)")
    ap.add_argument("--note", default="", help="what was checked")
    ap.add_argument("--documents", nargs="*", help="default: every rendered document")
    args = ap.parse_args(argv)

    if args.record == args.check:
        ap.error("give exactly one of --check and --record")
    if args.record:
        if not args.by:
            ap.error("--record needs --by")
        if not args.note:
            ap.error("--record needs --note: a review with no account of what was "
                     "checked is not evidence")
        return record(args.by, args.role, args.note, args.documents)
    return check(args.role)


if __name__ == "__main__":
    raise SystemExit(main())
