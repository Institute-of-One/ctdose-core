"""The archive the paper cites must contain the results the paper reports.

``tests/test_release_metadata.py`` checks that the version and the two DOIs agree with
one another across the README, ``CITATION.cff`` and the manuscript. They did, and the
snapshot they all agreed on was taken on 2026-07-28, before the revision that added the
GE validation arm: it has no ``results/ge_rdsr_accuracy.json``, and every other result
file in it differs from the one the manuscript was built from. Agreeing on a stale
archive is still stale.

``tools/check_release_contents.py`` does the work, because it needs the network and has
to fetch the archive. This reads the report it leaves behind, and refuses a report that
is older than the results it vouches for.

**This gate is expected to fail until a new release is cut and archived.** That is the
state of the submission, not a defect in the test: the reader of the Data Availability
statement would be sent to a snapshot that cannot reproduce the paper.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REPORT = REPO / "paper" / "release_check.json"

RERUN = "run: python tools/check_release_contents.py"

spec = importlib.util.spec_from_file_location(
    "release_contents", REPO / "tools" / "check_release_contents.py"
)
rc = importlib.util.module_from_spec(spec)
sys.modules["release_contents"] = rc
spec.loader.exec_module(rc)


@pytest.fixture(scope="module")
def report() -> dict:
    if not REPORT.exists():
        pytest.fail(f"the cited release has never been checked. {RERUN}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_report_is_not_stale(report: dict) -> None:
    now = hashlib.sha256(
        json.dumps(rc.digests_of_results(), sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert report["results_sha256"] == now, (
        f"the result files have changed since the release was checked. {RERUN}"
    )


def test_the_cited_version_is_the_archived_one(report: dict) -> None:
    assert report["cited_version"] == report["archived_version"], (
        f"the manuscript cites version {report['cited_version']} and the archive "
        f"{report['cited_record']} holds {report['archived_version']}"
    )


def test_line_endings_are_normalised_but_content_is_not() -> None:
    """The comparison has to ignore CRLF and nothing else.

    A Windows checkout holds the result files with CRLF; the source archive GitHub
    builds from the tag carries the LF that git stores. The first version of this check
    compared raw bytes and reported all eleven files as different from an archive that
    held exactly the right content -- a false alarm is how a gate gets switched off.
    Normalising is only safe if it still sees a real difference, so both directions are
    asserted here.
    """
    body = b'{"median": -5.7}\n'
    assert rc.digest(body) == rc.digest(body.replace(b"\n", b"\r\n")), (
        "a line-ending difference is reported as a content difference"
    )
    assert rc.digest(body) != rc.digest(b'{"median": -5.8}\n'), (
        "a changed value is not reported"
    )
    # Binary content is compared byte for byte, CR included.
    binary = b"\x89PNG\r\n\x1a\n\x00\x01"
    assert rc.digest(binary) != rc.digest(binary.replace(b"\r\n", b"\n"))


def test_the_comparison_reports_a_missing_and_a_changed_file() -> None:
    here = {"results/a.json": "aaa", "results/b.json": "bbb"}
    missing, stale = rc.compare(here, {"results/b.json": "ccc"})
    assert missing == ["results/a.json"]
    assert stale == ["results/b.json"]
    assert rc.compare(here, here) == ([], [])


def test_the_archive_holds_every_result_the_manuscript_reports(report: dict) -> None:
    missing = report["results_missing_from_the_archive"]
    stale = report["results_differing_from_the_archive"]
    assert not missing and not stale, (
        "the archive the Data Availability statement names cannot reproduce this "
        f"manuscript: {len(missing)} result file(s) absent {missing}, "
        f"{len(stale)} that differ {stale}. Cut and archive a new release, update the "
        f"title page, CITATION.cff and the README, then {RERUN}"
    )
