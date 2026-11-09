"""Prove the reference checker by injection.

A checker that passes on a correct manuscript has shown nothing. Each defect this gate
exists to catch is put into a copy of the manuscript, one at a time, and the checker has
to refuse it. The list is the history of what has actually gone wrong in this programme:
a DOI that points at a different paper (IORN-009, two of twenty-one), a dead DOI
(IORN-001), a year or volume written from memory, an affiliation recalled rather than
read, and -- new here -- an override that keeps printing a correction after the registry
has been fixed.

The real records are fetched once and replayed, so the proof is fast, deterministic and
does not hammer the registries; the unresolvable-DOI case is the one injection that is
allowed to miss the cache, since that is the behaviour under test.

Run::

    python tools/prove_check_references.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cref", REPO / "tools" / "check_references.py")
cref = importlib.util.module_from_spec(spec)
sys.modules["cref"] = cref
spec.loader.exec_module(cref)

MANUSCRIPT = REPO / "paper" / "ctdose-core_jacmp_technote.md"
OVERRIDES = REPO / "paper" / "reference_overrides.json"
REPORT = REPO / "paper" / "reference_check.json"

CACHE: dict[str, dict] = {}
_real_fetch = cref.fetch


def caching_fetch(url: str, accept: str) -> dict:
    if url not in CACHE:
        CACHE[url] = _real_fetch(url, accept)
    return CACHE[url]


def run(markdown: str, overrides: dict, title_page: str) -> tuple[int, dict]:
    """Run the checker against injected sources, leaving the real files alone."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        source = tmp / "manuscript.md"
        source.write_text(markdown, encoding="utf-8")
        page = tmp / "title_page.md"
        page.write_text(title_page, encoding="utf-8")
        over = tmp / "overrides.json"
        over.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
        report = tmp / "report.json"

        saved = (cref.OVERRIDES, cref.TITLE_PAGE, cref.REPORT)
        cref.OVERRIDES, cref.TITLE_PAGE, cref.REPORT = over, page, report
        try:
            code = cref.main(["--manuscript", str(source)])
            return code, json.loads(report.read_text(encoding="utf-8"))
        finally:
            cref.OVERRIDES, cref.TITLE_PAGE, cref.REPORT = saved


def main() -> int:
    cref.fetch = caching_fetch
    good_md = MANUSCRIPT.read_text(encoding="utf-8")
    good_over = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    good_page = cref.TITLE_PAGE.read_text(encoding="utf-8")

    print("warming the cache on the real manuscript (and checking it passes)")
    code, report = run(good_md, good_over, good_page)
    if code != 0 or report["status"] != "PASS":
        print("REFUSED: the real manuscript does not pass, so nothing below means anything")
        for e in report["errors"]:
            print(f"  {e}")
        return 2
    print(f"  clean run: {report['resolved']} DOI(s), status {report['status']}\n")

    injections: list[tuple[str, str, dict, str]] = [
        (
            "a DOI that resolves but points at a different paper",
            good_md.replace("doi:10.1148/radiol.10100047",
                            "doi:10.1007/s10278-013-9622-7"),
            good_over, good_page,
        ),
        (
            "a DOI that does not resolve at all",
            good_md.replace("doi:10.1148/radiol.10100047",
                            "doi:10.1148/radiol.99999999"),
            good_over, good_page,
        ),
        (
            "a year written from memory",
            good_md.replace("2010;257(1):158", "2011;257(1):158"),
            good_over, good_page,
        ),
        (
            "a volume written from memory",
            good_md.replace("2013;26(6):1045", "2013;27(6):1045"),
            good_over, good_page,
        ),
        (
            "a page range written from memory",
            good_md.replace("257(1):158–166", "257(1):158–165"),
            good_over, good_page,
        ),
        (
            "the first author of a reference wrong",
            good_md.replace("13. Clark K, Vendt B", "13. Clarke K, Vendt B"),
            good_over, good_page,
        ),
        (
            "an affiliation recalled instead of read (postcode dropped)",
            good_md, good_over,
            good_page.replace("Institute of One, LISIT Co., Ltd., Tokyo 150-0044, Japan",
                              "Institute of One, LISIT Co., Ltd., Tokyo, Japan"),
        ),
        (
            "a translated title printed with no override to justify it",
            good_md,
            {k: v for k, v in good_over.items() if k.startswith("_")},
            good_page,
        ),
        (
            "an override still printing a correction after the registry was fixed",
            good_md,
            {
                **good_over,
                "10.1055/s-2002-35937": {
                    **good_over["10.1055/s-2002-35937"],
                    "registered_title": "CT-Expo - a novel program for dose evaluation in CT",
                },
            },
            good_page,
        ),
    ]

    missed: list[str] = []
    for what, markdown, overrides, page in injections:
        code, report = run(markdown, overrides, page)
        caught = code != 0 and report["status"] == "FAIL"
        print(f"  {'caught ' if caught else 'MISSED '} {what}")
        if caught:
            for e in report["errors"][:1]:
                print(f"            -> {e.get('error')}")
        else:
            missed.append(what)

    # The proof must not have left the real report behind.
    on_disk = json.loads(REPORT.read_text(encoding="utf-8"))
    if on_disk["status"] != "PASS":
        print("\nREFUSED: the injections overwrote paper/reference_check.json")
        return 2

    if missed:
        print(f"\nREFUSED: {len(missed)} injection(s) were not caught:")
        for m in missed:
            print(f"  - {m}")
        return 1
    print(f"\nall {len(injections)} injections refused; the real manuscript passes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
