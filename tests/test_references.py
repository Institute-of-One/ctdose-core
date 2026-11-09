"""The reference check must have been run, have passed, and still apply.

``tools/check_references.py`` needs the network, so the suite does not re-run it; it
reads the report that run left behind. That is only worth anything if the report is tied
to the thing it vouches for -- otherwise a reference can be edited after a green run and
the gate keeps reporting the old result, which is how a stale check becomes a silent one.
So the report records the sha256 of the reference list and of the affiliation line, and
this compares them with what is in the manuscript now.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REPORT = REPO / "paper" / "reference_check.json"
MANUSCRIPT = REPO / "paper" / "ctdose-core_jacmp_technote.md"

RERUN = "run: python tools/check_references.py"

spec = importlib.util.spec_from_file_location(
    "cref", REPO / "tools" / "check_references.py"
)
cref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cref)


@pytest.fixture(scope="module")
def report() -> dict:
    if not REPORT.exists():
        pytest.fail(f"no reference check has ever been run. {RERUN}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_every_doi_resolved_and_agreed(report: dict) -> None:
    assert report["status"] == "PASS", (
        f"the last reference check failed: {report['errors']}"
    )
    assert report["errors"] == []


def test_the_check_covered_every_reference(report: dict) -> None:
    entries = cref.all_entries()
    assert any(label.startswith("S") for label, _ in entries), (
        "the supplement's own reference list is not being read; it is uploaded too, "
        "and one of its DOIs is cited nowhere else"
    )
    assert report["references"] == len(entries), (
        f"the manuscript now has {len(entries)} references, the check saw "
        f"{report['references']}. {RERUN}"
    )
    assert report["with_doi"] > 0, "no reference carried a DOI, so nothing was resolved"
    assert report["resolved"] == report["with_doi"], (
        "some DOI was printed but never resolved"
    )


def test_the_report_is_not_stale(report: dict) -> None:
    now = cref.entries_digest(cref.all_entries())
    assert report["references_sha256"] == now, (
        f"the reference list has changed since it was checked. {RERUN}"
    )


def test_the_affiliation_was_read_from_crossref(report: dict) -> None:
    affiliation = report["affiliation"]
    assert affiliation["ok"], (
        f"the title page's affiliation {affiliation['used_on_the_title_page']!r} "
        f"matches no Crossref record; Crossref carries "
        f"{affiliation['distinct_registered']}"
    )
    assert affiliation["records_matching"] >= 1
    now = hashlib.sha256(
        cref.affiliation_on_title_page().encode("utf-8")
    ).hexdigest()
    assert report["affiliation_sha256"] == now, (
        f"the affiliation on the title page has changed since it was checked. {RERUN}"
    )
