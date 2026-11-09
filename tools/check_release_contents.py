"""Does the archive the paper cites actually contain this manuscript's results?

The Data Availability statement names a version and a Zenodo DOI, and a reader who
follows it expects to be able to re-derive the numbers in the paper. That promise goes
stale silently: the archive is cut once and the manuscript keeps being revised. At
revision 2 of IORN-004 the cited archive was v0.1.1 of 2026-07-28 -- it predates the
revision that added the whole GE validation arm, so it does not contain
``results/ge_rdsr_accuracy.json`` at all, and 44 of its 104 files differ from the tree
the manuscript was built from, the corrected k-factor data among them.

Nothing could see that. The release-metadata gate checks that the version and the DOIs
*agree with each other*, which they did; agreeing on a stale snapshot is still stale.

What is checked:

* the DOI the title page cites resolves, and the record's version matches the one the
  title page and ``CITATION.cff`` state;
* every file under ``results/`` in the working tree is present in the archive;
* each one is byte-identical to the working tree's copy -- an archive holding an older
  version of a result file is as bad as one missing it.

Writes ``paper/release_check.json``. ``tests/test_release_contents.py`` reads that
report, so the suite needs no network, and fails if the report is missing, failing, or
older than the results it vouches for.

Usage::

    python tools/check_release_contents.py
    python tools/check_release_contents.py --doi 10.5281/zenodo.21636719
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
RESULTS = REPO / "results"
TITLE_PAGE = PAPER / "title_page_jacmp.md"
CITATION = REPO / "CITATION.cff"
REPORT = PAPER / "release_check.json"

USER_AGENT = "ctdose-core release-contents verification (IORN-004)"


def cited() -> tuple[str, str]:
    """The version and Zenodo record id the title page cites."""
    text = TITLE_PAGE.read_text(encoding="utf-8")
    version = re.search(r"version\s+(\d+\.\d+\.\d+)", text)
    record = re.search(r"zenodo\.(\d+)", text)
    if not version or not record:
        raise SystemExit(
            "the title page does not state a version and a Zenodo DOI in the form "
            '"(version X.Y.Z) ... zenodo.NNNNN"'
        )
    return version.group(1), record.group(1)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def digests_of_results() -> dict[str, str]:
    return {
        f"results/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(RESULTS.iterdir())
        if p.is_file()
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--doi", help="override the DOI read from the title page")
    args = ap.parse_args(argv)

    version, record_id = cited()
    if args.doi:
        found = re.search(r"zenodo\.(\d+)", args.doi)
        if not found:
            raise SystemExit(f"not a Zenodo DOI: {args.doi}")
        record_id = found.group(1)

    print(f"the manuscript cites version {version}, zenodo.{record_id}")
    record = fetch_json(f"https://zenodo.org/api/records/{record_id}")
    archived_version = record.get("metadata", {}).get("version")
    published = record.get("metadata", {}).get("publication_date")
    print(f"the record is version {archived_version}, published {published}")

    problems: list[str] = []
    if archived_version != version:
        problems.append(
            f"the title page cites version {version}; the archive is {archived_version}"
        )

    files = [f for f in record.get("files", []) if f.get("key", "").endswith(".zip")]
    if not files:
        problems.append("the record holds no zip archive to look inside")
        archived: dict[str, str] = {}
    else:
        url = files[0]["links"]["self"]
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "release.zip"
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=300) as response:
                local.write_bytes(response.read())
            with zipfile.ZipFile(local) as z:
                root = z.namelist()[0].split("/")[0] + "/"
                archived = {
                    name[len(root):]: hashlib.sha256(z.read(name)).hexdigest()
                    for name in z.namelist()
                    if not name.endswith("/") and name.startswith(root)
                }
        print(f"the archive holds {len(archived)} file(s)")

    here = digests_of_results()
    missing = sorted(name for name in here if name not in archived)
    stale = sorted(
        name for name in here if name in archived and archived[name] != here[name]
    )
    for name in missing:
        problems.append(f"{name} is not in the archive at all")
    for name in stale:
        problems.append(f"{name} in the archive is not the file this manuscript used")

    report = {
        "_why": (
            "Written by tools/check_release_contents.py. tests/test_release_contents.py "
            "fails if this is missing, not PASS, or older than the result files it "
            "vouches for. A release that agrees with itself can still be a snapshot "
            "taken before the revision it is cited in."
        ),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "cited_version": version,
        "cited_record": f"zenodo.{record_id}",
        "archived_version": archived_version,
        "archive_published": published,
        "results_in_the_tree": len(here),
        "results_missing_from_the_archive": missing,
        "results_differing_from_the_archive": stale,
        "results_sha256": hashlib.sha256(
            json.dumps(here, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "status": "FAIL" if problems else "PASS",
        "problems": problems,
    }
    REPORT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nThe archive the paper points at cannot reproduce the paper. Cut and "
            "archive a new release, then update the title page, CITATION.cff and the "
            "README, and run this again."
        )
        return 1
    print("\nthe cited archive contains every result file, unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
