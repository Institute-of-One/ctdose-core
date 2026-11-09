"""Somebody has to have looked at the pages, and at *these* pages.

The suite requires a current ``agent`` review: the rendered pages were read in a session
and the mechanical defects -- a stranded heading, a caption parted from its table, a
figure carrying another figure's number, a hyphen where a minus sign belongs -- were
looked for.

It does **not** require the author's review, because that would be red from the moment
anything is rebuilt until he next sits down, and a gate that is always red is a gate that
gets ignored. His review is a submission step, checked by
``python tools/visual_review.py --check --role author`` and listed in the kit.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
INDEX = PAPER / "build" / "pages" / "index.json"

spec = importlib.util.spec_from_file_location("vr", REPO / "tools" / "visual_review.py")
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

spec = importlib.util.spec_from_file_location(
    "render_pages", REPO / "tools" / "render_pdf_pages.py"
)
render_pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_pages)


@pytest.fixture(scope="module")
def index() -> dict:
    if not INDEX.exists():
        # paper/build/ is a build directory and is gitignored, so a fresh clone has no
        # page images and nothing here can be checked. Skipping is honest; failing
        # would make every clone red for not having run a build.
        pytest.skip("no page images in this checkout; run python paper/build_all.py")
    return json.loads(INDEX.read_text(encoding="utf-8"))


def test_every_uploaded_pdf_is_rendered(index: dict) -> None:
    """The kit's upload table is the list that matters; nothing on it may go unseen."""
    kit = (PAPER / "jacmp_submission_kit.md").read_text(encoding="utf-8")
    table = kit[kit.index("## 3. Files to upload") : kit.index("## 4.")]
    # Table rows only. The prose below the table names the files that must NOT be
    # uploaded -- the unmasked canonical PDF among them -- and reading those as uploads
    # would demand a review of a document the journal never sees.
    uploaded = {
        line.split("`paper/")[1].split("`")[0]
        for line in table.splitlines()
        if line.startswith("|") and "`paper/" in line
    }
    pdfs = {name for name in uploaded if name.endswith(".pdf")}
    assert pdfs, "no PDF was read out of the kit's upload table; the table has moved"
    missing = sorted(pdfs - set(index["documents"]))
    assert not missing, (
        f"uploaded but never rendered, so nobody can have looked at the pages: {missing}"
    )


def test_the_page_images_match_the_built_pdfs(index: dict) -> None:
    """A review of last week's pages says nothing about this week's document."""
    import hashlib

    stale = []
    for name, record in index["documents"].items():
        pdf = PAPER / name
        if not pdf.exists():  # correspondence is gitignored; a clone has neither
            continue
        now = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if now != record["pdf_sha256"]:
            stale.append(name)
    assert not stale, (
        f"these PDFs have been rebuilt since their pages were rendered: {stale}. "
        f"Run: python tools/render_pdf_pages.py"
    )


def test_the_page_images_are_on_disk(index: dict) -> None:
    missing = [
        page["image"]
        for record in index["documents"].values()
        for page in record["pages"]
        if not (PAPER / page["image"]).exists()
    ]
    assert not missing, f"{len(missing)} page image(s) listed but absent: {missing[:5]}"


def test_an_agent_review_is_current(index: dict) -> None:
    report = vr.status(index=index)
    not_current = {
        name: entry["by_role"]["agent"]
        for name, entry in report.items()
        if entry["by_role"]["agent"]["state"] != "current"
    }
    assert not not_current, (
        "no current record that anyone looked at the rendered pages of: "
        + ", ".join(
            f"{name} ({state['state']})" for name, state in sorted(not_current.items())
        )
        + ". Look at paper/build/pages/<stem>/page*.png and then run: "
          'python tools/visual_review.py --record --by "..." --role agent --note "..."'
    )


def test_the_ledger_goes_stale_when_a_page_changes(index: dict) -> None:
    """Non-vacuity: a review must stop counting when the pages move.

    Pinning the PDF's own bytes would have been the obvious thing to record, and in a
    repository where the builder writes a fresh timestamp every run it is also useless.
    This checks the opposite failure -- that the ledger is not so forgiving that a
    changed page still reads as reviewed.
    """
    name = next(iter(index["documents"]))
    doctored = {
        "reviews": [
            {
                "by": "nobody",
                "role": "agent",
                "at": "1970-01-01",
                "documents": {
                    name: {
                        "page_sha256": [
                            "0" * 64,
                            *vr.current_pages(index, name)[1:],
                        ]
                    }
                },
            }
        ]
    }
    state = vr.status(index=index, ledger=doctored)[name]["by_role"]["agent"]
    assert state["state"] == "stale"
    assert state["pages_changed"] == [1], (
        f"the ledger did not point at the page that changed: {state}"
    )
