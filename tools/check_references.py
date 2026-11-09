"""Resolve every DOI the manuscript prints, and check the author's affiliation.

Adapted from ``pcct-vmi-core/submission/jxst/check_references.py`` (IORN-016), itself
from ctp-core. The rule it exists to enforce is that no bibliographic fact is written
from memory:

* **No DOI is inferred from a pattern.** In IORN-009 two of twenty-one DOIs pointed at a
  different paper; in IORN-001 a reference was a dead link. Every DOI here is fetched and
  the registered year, volume, issue, pages, first author and title are compared against
  what the manuscript prints.
* **The affiliation is read from Crossref, not recalled.** The canonical string is
  whatever the author's own published records carry, and getting it wrong at proof stage
  has nearly put an inconsistent affiliation into print.

Where the registry is itself wrong or incomplete, ``paper/reference_overrides.json``
records the correction **together with the condition that justifies it** -- the string
that is registered today. If the publisher corrects the record, the condition stops
holding and this script fails, rather than silently printing a stale correction.

Writes ``paper/reference_check.json``. ``tests/test_references.py`` reads that file and
fails if it is missing, failing, or older than the reference list it vouches for, so the
suite never needs the network but also never passes on a stale result.

Usage::

    python tools/check_references.py
    python tools/check_references.py --manuscript paper/ctdose-core_jacmp_technote.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
MANUSCRIPT = PAPER / "ctdose-core_jacmp_technote.md"
SUPPLEMENT = PAPER / "ctdose-core_jacmp_technote_supplement.md"
TITLE_PAGE = PAPER / "title_page_jacmp.md"
OVERRIDES = PAPER / "reference_overrides.json"
REPORT = PAPER / "reference_check.json"

ORCID = "0000-0001-9211-1071"
USER_AGENT = "ctdose-core manuscript reference verification (IORN-004)"

DOI = re.compile(r"doi:\s*(10\.\d{4,9}/\S+?)\s*$", re.I)
#: "2026;27(2):e70473" and "2010;257(1):158-166", with either hyphen or en dash.
CITED = re.compile(r"(?P<year>\d{4});(?P<volume>\d+)\((?P<issue>[^)]+)\):(?P<pages>\S+?)\.")


def normalise(text: str) -> str:
    """Letters and digits only.

    Crossref keeps the publisher's markup in the title, so reference 7 arrives as
    ``Survey of normalized CTDI\\n <sub>vol</sub>\\n values ...``. Dropping the tags
    leaves the indentation behind as spaces, and "CTDI vol" then differs from the
    manuscript's "CTDIvol" on a difference that is not a difference. Separators carry no
    information worth comparing here -- two titles that agree letter for letter once the
    punctuation is gone are the same title -- while a wrong paper, a wrong year or a
    translated title all still differ.
    """
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


#: The heading that opens a reference list, in the manuscript or in the supplement.
#: The supplement numbers its entries S1, S2, ... and its list went unchecked entirely
#: until 2026-11-09: it carries two DOIs, and one of them is cited nowhere else.
REFERENCE_HEADING = re.compile(r"^##\s+(?:S\d+\.\s+Supplementary references|References)\s*$")


def references_of(markdown: str) -> list[tuple[str, str]]:
    """The numbered entries of the reference list, in order, keyed by their label."""
    lines = markdown.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if REFERENCE_HEADING.match(line.strip())),
        None,
    )
    if start is None:
        raise SystemExit("this document has no reference list")
    out: list[tuple[str, str]] = []
    for line in lines[start + 1 :]:
        line = line.strip()
        if line.startswith("---") or line.startswith("## "):
            break
        m = re.match(r"^(S?\d+)\.\s+(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def all_entries(manuscript: Path = MANUSCRIPT) -> list[tuple[str, str]]:
    """Every reference the submission prints: the manuscript's list and the supplement's.

    The supplement is uploaded too and keeps a reference list of its own, which nothing
    checked until 2026-11-09. One of its two DOIs is cited nowhere else.
    """
    documents = [manuscript]
    if manuscript == MANUSCRIPT and SUPPLEMENT.is_file():
        documents.append(SUPPLEMENT)
    return [
        (label, entry)
        for doc in documents
        for label, entry in references_of(doc.read_text(encoding="utf-8"))
    ]


def entries_digest(entries: list[tuple[str, str]]) -> str:
    return hashlib.sha256(
        "\n".join(f"{n}. {e}" for n, e in entries).encode("utf-8")
    ).hexdigest()


def cited_title(entry: str) -> str | None:
    """The title as printed: what sits between the authors and the italic journal."""
    before = entry.split("*", 1)[0]
    parts = [p.strip() for p in before.split(". ") if p.strip()]
    return parts[-1] if len(parts) >= 2 else None


def fetch(url: str, accept: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def check_affiliation(used: str) -> dict:
    """The affiliation the title page prints must be one Crossref actually carries."""
    data = fetch(
        "https://api.crossref.org/works?"
        f"filter=orcid:{ORCID}&rows=100&select=DOI,author,issued",
        "application/json",
    )
    registered: list[str] = []
    for work in data["message"]["items"]:
        for author in work.get("author", []):
            if (author.get("ORCID") or "").endswith(ORCID):
                for affiliation in author.get("affiliation", []):
                    name = affiliation.get("name")
                    if name:
                        registered.append(name)
    matches = [name for name in registered if normalise(name) == normalise(used)]
    return {
        "used_on_the_title_page": used,
        "records_found": len(registered),
        "records_matching": len(matches),
        "distinct_registered": sorted(set(registered)),
        "ok": bool(matches),
    }


def affiliation_on_title_page() -> str:
    for line in TITLE_PAGE.read_text(encoding="utf-8").splitlines():
        if line.startswith("Institute of One"):
            return line.strip()
    raise SystemExit("no affiliation line found on the title page")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manuscript", type=Path, default=MANUSCRIPT)
    args = ap.parse_args(argv)

    # The supplement is uploaded too and has a reference list of its own, which nothing
    # checked until 2026-11-09. Both lists are walked; a label is prefixed with the
    # document so a report names which list an entry is in.
    entries = all_entries(args.manuscript)
    overrides = {
        k: v
        for k, v in json.loads(OVERRIDES.read_text(encoding="utf-8")).items()
        if not k.startswith("_")
    } if OVERRIDES.exists() else {}

    records: list[dict] = []
    errors: list[dict] = []
    with_doi = 0

    for number, entry in entries:
        found = DOI.search(entry)
        if not found:
            records.append({"n": number, "doi": None, "note": "no DOI printed"})
            continue
        with_doi += 1
        doi = found.group(1).rstrip(".")
        printed = CITED.search(entry)
        title = cited_title(entry)
        if title is None:
            errors.append({"n": number, "doi": doi,
                           "error": "could not read the printed title from the entry"})
            continue
        try:
            csl = fetch("https://doi.org/" + doi,
                        "application/vnd.citationstyles.csl+json")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            errors.append({"n": number, "doi": doi, "error": f"did not resolve: {exc}"})
            continue

        registered_title = csl.get("title")
        if isinstance(registered_title, list):
            registered_title = registered_title[0]
        # Both the online-first year and the issue year count. Crossref's ``issued`` is
        # the earliest, so S7 is registered as 2015 while the issue it appeared in --
        # and what PubMed and AMA style cite -- is Pediatr Radiol 2016;46(2). Insisting
        # on ``issued`` alone would have demanded a wrong year in the supplement.
        registered_years = {
            (csl.get(field, {}).get("date-parts") or [[None]])[0][0]
            for field in ("issued", "published-print", "published-online")
        } - {None}
        registered_year = (csl.get("issued", {}).get("date-parts") or [[None]])[0][0]
        authors = csl.get("author") or []
        registered_family = (authors[0].get("family") or "") if authors else ""

        problems: list[str] = []
        fix = overrides.get(doi, {})
        if "registered_title" in fix:
            # The override stands only while the registry is still wrong in the recorded
            # way. A publisher correction must break this, not be papered over.
            if normalise(fix["registered_title"]) != normalise(registered_title or ""):
                problems.append(
                    f"the title override no longer matches what is registered "
                    f"({registered_title!r}); re-read the record and update or drop it"
                )
            elif normalise(fix["printed_title"]) != normalise(title):
                problems.append(
                    "the manuscript's title does not match the override's printed form"
                )
        elif normalise(title) != normalise(registered_title or ""):
            problems.append(
                f"title printed as {title!r}, registered as {registered_title!r}"
            )

        if printed:
            if int(printed.group("year")) not in registered_years:
                problems.append(
                    f"year printed as {printed.group('year')}, registered as "
                    f"{sorted(registered_years)}"
                )
            for field, key in (("volume", "volume"), ("issue", "issue")):
                want = printed.group(field)
                got = str(csl.get(key, ""))
                if got and want != got:
                    problems.append(f"{field} printed as {want}, registered as {got}")
            pages, article = str(csl.get("page", "")), str(csl.get("article-number", ""))
            want = printed.group("pages").replace("–", "-")
            if pages and normalise(want) != normalise(pages):
                if not (article and normalise(want) == normalise(article)):
                    problems.append(
                        f"pages printed as {want}, registered as {pages}"
                        + (f" (article number {article})" if article else "")
                    )
        else:
            problems.append("no year;volume(issue):pages could be read from the entry")

        first_printed = entry.split()[0].rstrip(",")
        if registered_family and normalise(first_printed) != normalise(registered_family):
            if normalise(first_printed) != normalise(
                (fix.get("printed_first_author") or "")
            ):
                problems.append(
                    f"first author printed as {first_printed!r}, registered as "
                    f"{registered_family!r}"
                )

        record = {
            "n": number,
            "doi": doi,
            "registered_title": registered_title,
            "registered_year": registered_year,
            "printed_title": title,
            "resolved": True,
        }
        if fix:
            record["override_reason"] = fix.get("why")
        if problems:
            errors.append({"n": number, "doi": doi, "error": "; ".join(problems)})
            print(f"  [{number}] {doi}  MISMATCH")
            for p in problems:
                print(f"        {p}")
        else:
            print(f"  [{number}] {doi}  ok  {registered_year}  {registered_title}")
        records.append(record)

    print()
    affiliation = check_affiliation(affiliation_on_title_page())
    if affiliation["ok"]:
        print(
            f"  affiliation ok: {affiliation['records_matching']} of "
            f"{affiliation['records_found']} Crossref author records carry it verbatim"
        )
    else:
        print("  affiliation MISMATCH. Crossref carries:")
        for name in affiliation["distinct_registered"]:
            print(f"        {name}")
        errors.append({
            "error": "the title page's affiliation matches no Crossref record",
            "used": affiliation["used_on_the_title_page"],
        })

    report = {
        "_why": (
            "Written by tools/check_references.py. tests/test_references.py fails if "
            "this is missing, not PASS, or older than the reference list and "
            "affiliation it vouches for."
        ),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "manuscript": (
            args.manuscript.relative_to(REPO).as_posix()
            if args.manuscript.is_relative_to(REPO)
            else str(args.manuscript)  # an injected copy, under tools/prove_*
        ),
        "references_sha256": entries_digest(entries),
        "affiliation_sha256": hashlib.sha256(
            affiliation["used_on_the_title_page"].encode("utf-8")
        ).hexdigest(),
        "references": len(entries),
        "with_doi": with_doi,
        "resolved": sum(1 for r in records if r.get("resolved")),
        "affiliation": affiliation,
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
    }
    REPORT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"\n{report['resolved']}/{with_doi} DOI(s) resolved and agree, "
        f"{len(entries)} reference(s) in all -- {report['status']}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
