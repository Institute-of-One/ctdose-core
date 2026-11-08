"""Record what was typed into the submission portal, and pin it to the build.

    python paper/record_portal_fields.py --field novel_scientific_points --from-build
    python paper/record_portal_fields.py --check

Some things a journal asks for are *typed into a form*, not uploaded: the Novel Scientific
Points, the abstract, the cover letter box. Every gate in this repository reads files, and
a rebuild cannot reach a form field, so the two drift apart silently and nothing notices.

That happened to RPTE-D-26-00650. Four claims were corrected on 2026-11-07 at 21:59;
Figure 6 was rebuilt at 22:03 and the manuscript at 23:44, both after, and both correct in
the submitted package. The Novel Scientific Points had been typed earlier in the evening
and were never re-typed, so two of the five entries went to the journal carrying claims the
manuscript had withdrawn -- that a per-organ table "does not beat" one factor, which the
cohort does not establish, and that the residual is "physical", which the Monte-Carlo
uncertainty does not show. The manuscript was right and the metadata was wrong, and the
editors and reviewers read the metadata.

This records the sha256 of the text that was actually entered, alongside the sha256 of the
file the build produces. ``--check`` fails when they differ, which is both "the build moved
after you pasted" and "you pasted something else". Only digests are stored, so a field
holding anything sensitive is still safe to pin here.

Workflow: build, paste into the portal, then run this with ``--from-build`` *immediately
after pasting* -- or with ``--text-file`` pointing at what you actually pasted, if you want
the record to be of the paste rather than of the build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAPER = Path(__file__).resolve().parent
BUILD = PAPER / "build"
LEDGER = PAPER / "portal_fields.json"

#: Portal fields that are typed rather than uploaded, and the built file each must match.
FIELDS: dict[str, Path] = {
    "abstract": BUILD / "abstract_for_portal.txt",
    "cover_letter": BUILD / "cover_letter_for_portal.txt",
}


def digest(text: str) -> str:
    """Hash the text as the portal would hold it: trailing space and CR are noise."""
    normalised = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    return hashlib.sha256(normalised.strip().encode("utf-8")).hexdigest()


def load() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.is_file() else {}


def record(field: str, text: str, note: str) -> int:
    source = FIELDS[field]
    if not source.is_file():
        print(f"{source} has not been built; build before recording")
        return 1
    ledger = load()
    ledger[field] = {
        "_why": (
            "What was entered into the submission portal for this field. Portal fields "
            "are typed, not uploaded, so no rebuild reaches them and no file-reading "
            "gate sees them. Only digests are stored."
        ),
        "entered_sha256": digest(text),
        "build_sha256": digest(source.read_text(encoding="utf-8")),
        "built_from": source.name,
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": note,
    }
    LEDGER.write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    agrees = ledger[field]["entered_sha256"] == ledger[field]["build_sha256"]
    print(f"recorded {field}: entered text {'matches' if agrees else 'DIFFERS FROM'} the build")
    return 0 if agrees else 1


def check() -> int:
    ledger = load()
    if not ledger:
        print("no portal field has been recorded; run with --field ... --from-build")
        return 1
    bad = 0
    for field, source in FIELDS.items():
        entry = ledger.get(field)
        if entry is None:
            print(f"  {field}: never recorded")
            bad += 1
            continue
        if not source.is_file():
            print(f"  {field}: {source.name} is not built")
            bad += 1
            continue
        current = digest(source.read_text(encoding="utf-8"))
        if entry["entered_sha256"] != current:
            print(
                f"  {field}: what was entered on {entry['recorded_utc']} is not what "
                f"{source.name} now says. Re-paste it into the portal and re-record."
            )
            bad += 1
        else:
            print(f"  {field}: matches {source.name}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--field", choices=sorted(FIELDS))
    ap.add_argument("--from-build", action="store_true",
                    help="record the built text as what was entered")
    ap.add_argument("--text-file", type=Path, help="record this file's text as entered")
    ap.add_argument("--note", default="")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return check()
    if not args.field:
        ap.error("give --field, or --check")
    if args.text_file:
        return record(args.field, args.text_file.read_text(encoding="utf-8"), args.note)
    if args.from_build:
        return record(args.field, FIELDS[args.field].read_text(encoding="utf-8"), args.note)
    ap.error("give --from-build or --text-file")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
