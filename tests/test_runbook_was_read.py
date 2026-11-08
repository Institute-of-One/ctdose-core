"""The submission runbook must have been read, at the version now on disk.

AGENTS.md and the runbook's own section 11 both say to read sections 2 and 11 before
starting on a manuscript, and AGENTS.md adds that this gate travels to every new
repository. An instruction that is only an instruction gets skipped; this one is checked.

The runbook grows a section every time a round trip is lost, so a runbook that has been
extended since it was read is a runbook with unread sections in it. The digest covers
that case as well as never having read it at all.

It lives outside any public repository, so a clone will not have it. There the test
reports that it cannot tell, rather than failing: a gate that fails for everyone who did
nothing wrong gets switched off.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "paper"))

import record_runbook_read as runbook  # noqa: E402


def test_the_runbook_was_read_at_the_version_on_disk():
    if not runbook.RUNBOOK.is_file():
        pytest.skip(
            f"{runbook.RUNBOOK.name} is not on this machine, so whether it was read "
            "cannot be checked here"
        )

    assert runbook.LEDGER.is_file(), (
        f"no record that {runbook.RUNBOOK.name} was read; read sections "
        f"{', '.join(runbook.REQUIRED_SECTIONS)} and run paper/record_runbook_read.py"
    )
    ledger = json.loads(runbook.LEDGER.read_text(encoding="utf-8"))

    assert ledger.get("sha256") == runbook.digest(runbook.RUNBOOK), (
        f"{runbook.RUNBOOK.name} has changed since it was read on "
        f"{ledger.get('read_on')}; read it again and re-record. A runbook grows a "
        f"section every time a round trip is lost, so the sections that were added are "
        f"exactly the ones that have not been read"
    )

    missing = set(runbook.REQUIRED_SECTIONS) - set(ledger.get("sections", []))
    assert not missing, (
        f"the ledger does not record section(s) {sorted(missing)} as read"
    )


def test_the_ledger_stores_no_runbook_content():
    """The runbook is not public; only its digest and the date may be recorded here."""
    if not runbook.LEDGER.is_file():
        pytest.skip("nothing recorded yet")
    ledger = json.loads(runbook.LEDGER.read_text(encoding="utf-8"))
    assert set(ledger) <= {
        "_why", "runbook", "sha256", "sections", "read_on", "by", "note",
    }, f"unexpected field(s) in the ledger: {sorted(set(ledger) - {'_why'})}"
    assert ledger["runbook"] == runbook.RUNBOOK.name
    # A digest, not an excerpt.
    assert len(ledger["sha256"]) == 64 and all(
        c in "0123456789abcdef" for c in ledger["sha256"]
    )
