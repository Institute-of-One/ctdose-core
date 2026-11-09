"""Write the version, the release date and the version DOI into every place that has one.

Three facts are fixed by cutting a release -- the version, the date it was tagged and
the version DOI -- and in this repository each of them is written in several files. In
IORN-015 the same three facts lived in eight places and were typed eight times; the
gates then checked that the eight agreed, which is not the same as making them agree.
This writes them once.

**Two stages, and the order is not a preference.**

``--stage version``
    Run **before** the tag. Zenodo reads ``.zenodo.json`` from the commit the GitHub
    Release points at, and the version it records cannot be corrected afterwards, so
    the bump has to be in that commit.

``--stage doi``
    Run **after** the Release, once Zenodo has minted the version DOI. The DOI is
    resolved before it is written, and a DOI that is already in ``CITATION.cff`` is
    refused: that is the concept DOI, which follows whichever release is newest, and
    putting it in the manuscript is an accident no other check here would catch -- it
    resolves, it is a real DOI of this project, and the README is supposed to carry it.

Usage::

    python tools/apply_release.py --stage version --version 0.2.0 --date 2026-11-09
    python tools/apply_release.py --stage doi --doi 10.5281/zenodo.NNNNNNNN
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]

INIT = REPO / "ctdose_core" / "__init__.py"
PYPROJECT = REPO / "pyproject.toml"
ZENODO = REPO / ".zenodo.json"
CITATION = REPO / "CITATION.cff"
TITLE_PAGE = REPO / "paper" / "title_page_jacmp.md"
TECHNOTE = REPO / "paper" / "ctdose-core_jacmp_technote.md"
ARTICLE = REPO / "paper" / "ctdose-core_jacmp.md"
MASKER = REPO / "tools" / "build_masked_manuscript.py"
STATE = REPO / "docs" / "IORN-004_STATE.md"

#: Files whose prose names the version as "version X.Y.Z". The masking script holds its
#: own copy of the sentence it substitutes into the review-facing manuscript, so a bump
#: that misses it un-masks nothing but prints the wrong version to the reviewers.
PROSE = (TITLE_PAGE, TECHNOTE, ARTICLE, MASKER, STATE)

VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
DOI = re.compile(r"10\.5281/zenodo\.\d+")


def current_version() -> str:
    found = re.search(r'__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("no __version__ in ctdose_core/__init__.py")
    return found.group(1)


def concept_doi() -> str:
    zenodo = json.loads(ZENODO.read_text(encoding="utf-8"))
    return next(
        r["identifier"].replace("https://doi.org/", "")
        for r in zenodo["related_identifiers"]
        if r["relation"] == "isVersionOf"
    )


def cited_doi() -> str:
    """The version DOI the manuscript cites today."""
    found = DOI.findall(TITLE_PAGE.read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("the title page cites no Zenodo DOI")
    return found[0]


def replace_in(path: Path, old: str, new: str, *, expected: int | None = None) -> int:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if expected is not None and count != expected:
        raise SystemExit(
            f"{path.relative_to(REPO)}: expected {expected} occurrence(s) of {old!r}, "
            f"found {count}"
        )
    if count:
        path.write_text(text.replace(old, new), encoding="utf-8")
    return count


def stage_version(version: str, date: str) -> int:
    old = current_version()
    if old == version:
        raise SystemExit(f"the version is already {version}")
    if VERSION.fullmatch(version) is None:
        raise SystemExit(f"not a version: {version}")
    dt.date.fromisoformat(date)  # raises on a malformed date

    zenodo = json.loads(ZENODO.read_text(encoding="utf-8"))
    if "doi" in zenodo:
        raise SystemExit(
            ".zenodo.json has a top-level 'doi'. Zenodo stops minting a version DOI "
            "when it is present; remove it before cutting the release."
        )

    touched: list[str] = []
    for path, old_text, new_text, expected in (
        (INIT, f'__version__ = "{old}"', f'__version__ = "{version}"', 1),
        (PYPROJECT, f'version = "{old}"', f'version = "{version}"', 1),
        (ZENODO, f'"version": "{old}"', f'"version": "{version}"', 1),
        (CITATION, f"version: {old}", f"version: {version}", 1),
    ):
        replace_in(path, old_text, new_text, expected=expected)
        touched.append(path.relative_to(REPO).as_posix())

    # date-released is the tag date; Zenodo takes its own date from the Release, so this
    # is the human-readable copy and must not be left at the previous release's.
    # Whether the line is there is a different question from whether it changed: a
    # second release on the same day leaves it identical, and testing for a change
    # reported the line as missing and left the bump half applied.
    text = CITATION.read_text(encoding="utf-8")
    pattern = re.compile(r"date-released: '[^']*'")
    if not pattern.search(text):
        raise SystemExit("no date-released line in CITATION.cff")
    CITATION.write_text(
        pattern.sub(f"date-released: '{date}'", text, count=1), encoding="utf-8"
    )

    for path in PROSE:
        n = replace_in(path, f"version {old}", f"version {version}")
        if path is not STATE:
            # Not in the state document: it lists the tags that exist, and a blanket
            # "v0.1.1 -> v0.2.0" there rewrote the history row so that v0.1.1 -- the
            # release revision 1 cited -- had never existed. A record is not a
            # current-state field; only the latter is rewritten.
            n += replace_in(path, f"v{old}", f"v{version}")
        if n:
            touched.append(f"{path.relative_to(REPO).as_posix()} ({n})")
    if STATE.is_file():
        print(
            f"  note: {STATE.relative_to(REPO).as_posix()} lists the tags by hand; "
            f"add v{version} there and leave v{old} in the history"
        )

    print(f"version {old} -> {version}, date-released {date}")
    for name in touched:
        print(f"  {name}")
    print(
        "\nCommit this, then tag and release. The DOI goes in afterwards with "
        "--stage doi."
    )
    return 0


def stage_doi(doi: str) -> int:
    doi = doi.replace("https://doi.org/", "").strip()
    if DOI.fullmatch(doi) is None:
        raise SystemExit(f"not a Zenodo DOI: {doi}")

    concept = concept_doi()
    if doi == concept:
        raise SystemExit(
            f"{doi} is the concept DOI. It resolves to whichever release is newest, so "
            f"a reader who follows it after the next tag is handed code the results did "
            f"not come from. Use the version DOI of this release."
        )
    citation = CITATION.read_text(encoding="utf-8")
    if doi in citation:
        raise SystemExit(
            f"{doi} is already in CITATION.cff, which carries the concept DOI. "
            f"That is the substitution this check exists for."
        )

    request = urllib.request.Request(
        f"https://doi.org/{doi}",
        headers={"Accept": "application/vnd.citationstyles.csl+json",
                 "User-Agent": "ctdose-core release"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            record = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise SystemExit(f"{doi} does not resolve: {exc}") from exc
    version = current_version()
    registered = str(record.get("version") or "")
    if registered and registered.lstrip("v") != version:
        raise SystemExit(
            f"{doi} is registered as version {registered!r}; this tree is {version}"
        )

    old = cited_doi()
    if old == doi:
        raise SystemExit(f"the manuscript already cites {doi}")

    touched = []
    for path in PROSE:
        n = replace_in(path, old, doi)
        if n:
            touched.append(f"{path.relative_to(REPO).as_posix()} ({n})")

    print(f"version DOI {old} -> {doi}  (registered version {registered or 'n/a'})")
    for name in touched:
        print(f"  {name}")
    print(
        "\nThe concept DOI stays where it is: the README badge and CITATION.cff.\n"
        "Now rebuild, then run tools/check_release_contents.py."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=("version", "doi"), required=True)
    ap.add_argument("--version", help="the new version, with --stage version")
    ap.add_argument("--date", help="the tag date, YYYY-MM-DD, with --stage version")
    ap.add_argument("--doi", help="the version DOI, with --stage doi")
    args = ap.parse_args(argv)

    if args.stage == "version":
        if not args.version or not args.date:
            ap.error("--stage version needs --version and --date")
        return stage_version(args.version, args.date)
    if not args.doi:
        ap.error("--stage doi needs --doi")
    return stage_doi(args.doi)


if __name__ == "__main__":
    raise SystemExit(main())
