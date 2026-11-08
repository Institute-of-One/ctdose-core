"""Rebuild every artefact of the JACMP submission, in dependency order.

    python paper/build_all.py
    python paper/build_all.py --check    # fail if anything is out of date

The runbook's pre-submission checklist opens with a single build command, for a reason
this repository has now met twice: a document that is uploaded but not rebuilt carries
the previous revision's text while every gate that reads the Markdown stays green. The
manuscript gates here read the canonical Markdown; the masked manuscript, the
tracked-changes diff and all five PDFs are *derived*, and each is produced by a separate
script that has to be run with the right arguments in the right order.

Order matters in two places:

* the masked Technical Note must be built before the tracked-changes diff, because the
  diff compares the previously submitted masked manuscript with the new one;
* the frozen masked manuscript of the previous round is an input, never an output. It is
  what was uploaded last time, and rebuilding it would make the diff compare a document
  with itself -- which is empty, and passes.

Every artefact listed in ``ARTEFACTS`` is counted and reported, so a step that silently
stops running is visible as a missing line rather than as a document nobody rebuilt.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAPER = Path(__file__).resolve().parent
REPO = PAPER.parent
TOOLS = REPO / "tools"

MASK = TOOLS / "build_masked_manuscript.py"
PDF = TOOLS / "build_manuscript_pdf.py"
DIFF = TOOLS / "build_tracked_changes.py"

#: (description, command, the file it must produce). Order is dependency order.
STEPS: tuple[tuple[str, list[str], str], ...] = (
    (
        "masked Research Article (preserved; not submitted, but kept in step)",
        [str(MASK)],
        "ctdose-core_jacmp_masked.pdf",
    ),
    (
        "masked Technical Note -- the Article File",
        [
            str(MASK),
            "--source", "paper/ctdose-core_jacmp_technote.md",
            "--out-md", "paper/ctdose-core_jacmp_technote_masked.md",
            "--out-pdf", "paper/ctdose-core_jacmp_technote_masked.pdf",
        ],
        "ctdose-core_jacmp_technote_masked.pdf",
    ),
    (
        "tracked changes, against the masked manuscript submitted last round",
        [str(DIFF)],
        "ctdose-core_jacmp_technote_tracked_changes.pdf",
    ),
    (
        "canonical Technical Note (unmasked, for the author)",
        [str(PDF), "paper/ctdose-core_jacmp_technote.md",
         "--out", "paper/ctdose-core_jacmp_technote.pdf"],
        "ctdose-core_jacmp_technote.pdf",
    ),
    (
        "Supplementary Material",
        [str(PDF), "paper/ctdose-core_jacmp_technote_supplement.md",
         "--out", "paper/ctdose-core_jacmp_technote_supplement.pdf"],
        "ctdose-core_jacmp_technote_supplement.pdf",
    ),
    (
        "title page",
        [str(PDF), "paper/title_page_jacmp.md", "--out", "paper/title_page_jacmp.pdf"],
        "title_page_jacmp.pdf",
    ),
    (
        "point-by-point response",
        [str(PDF), "paper/response_to_editor_jacmp_r2.md",
         "--out", "paper/response_to_editor_jacmp_r2.pdf"],
        "response_to_editor_jacmp_r2.pdf",
    ),
    (
        "cover letter",
        [str(PDF), "paper/cover_letter_jacmp_r2.md",
         "--out", "paper/cover_letter_jacmp_r2.pdf"],
        "cover_letter_jacmp_r2.pdf",
    ),
)

#: Every built file this submission ships, and the source each is built from. Counted
#: against STEPS so an artefact without a build step cannot go unnoticed.
ARTEFACTS: dict[str, str] = {
    "ctdose-core_jacmp_technote.pdf": "ctdose-core_jacmp_technote.md",
    "ctdose-core_jacmp_technote_masked.md": "ctdose-core_jacmp_technote.md",
    "ctdose-core_jacmp_technote_masked.pdf": "ctdose-core_jacmp_technote.md",
    "ctdose-core_jacmp_technote_supplement.pdf": "ctdose-core_jacmp_technote_supplement.md",
    "ctdose-core_jacmp_technote_tracked_changes.md": "ctdose-core_jacmp_technote_masked.md",
    "ctdose-core_jacmp_technote_tracked_changes.pdf": "ctdose-core_jacmp_technote_masked.md",
    "title_page_jacmp.pdf": "title_page_jacmp.md",
    "response_to_editor_jacmp_r2.pdf": "response_to_editor_jacmp_r2.md",
    "cover_letter_jacmp_r2.pdf": "cover_letter_jacmp_r2.md",
    "ctdose-core_jacmp_masked.pdf": "ctdose-core_jacmp.md",
}

#: Inputs, not outputs. Rebuilding these would destroy what they record.
FROZEN = ("ctdose-core_jacmp_technote_masked_r1.md",)


def stale() -> list[str]:
    out = []
    for built, source in ARTEFACTS.items():
        b, s = PAPER / built, PAPER / source
        if not s.is_file():
            # The cover letter and the point-by-point response are correspondence and
            # are gitignored, so a clone has neither the source nor the built PDF.
            # Reporting that as staleness would cry wolf on every clone.
            out.append(f"{built}: its source {source} is not in this checkout")
            continue
        if not b.is_file():
            out.append(f"{built} has not been built")
        elif b.stat().st_mtime < s.stat().st_mtime:
            out.append(f"{built} is older than {source}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report staleness, build nothing")
    args = ap.parse_args(argv)

    for name in FROZEN:
        if not (PAPER / name).is_file():
            print(f"missing frozen input {name}; the tracked-changes diff needs it")
            return 1

    if args.check:
        problems = stale()
        for line in problems:
            print(f"  stale: {line}")
        print(f"{len(ARTEFACTS) - len(problems)}/{len(ARTEFACTS)} artefacts current")
        return 1 if problems else 0

    for description, command, produces in STEPS:
        result = subprocess.run(
            [sys.executable, *command], cwd=REPO, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"FAILED  {description}")
            print(result.stdout[-2000:])
            print(result.stderr[-2000:])
            return result.returncode
        print(f"  built  {produces:52} {description}")

    missing = sorted(set(ARTEFACTS) - {produces for _, _, produces in STEPS})
    derived = [m for m in missing if not (PAPER / m).is_file()]
    if derived:
        print(f"\nartefacts with no build step that produces them: {derived}")
        return 1

    problems = stale()
    if problems:
        print("\nstill out of date after building:")
        for line in problems:
            print(f"  {line}")
        return 1
    print(f"\nall {len(ARTEFACTS)} artefacts current, from {len(STEPS)} build steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
