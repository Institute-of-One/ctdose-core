"""The release metadata must agree with itself and with the manuscript.

A published paper cites a repository URL and an archived version. If `pyproject.toml`,
`CITATION.cff`, `.zenodo.json`, the package `__version__` and the manuscript disagree,
the citation a reader follows is the one that is wrong. These files drifted apart once
already -- the packaging URL named a GitHub organisation that does not exist while the
manuscript named one that does -- so the agreement is asserted rather than assumed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:  # tomllib is standard-library from Python 3.11; fall back on 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

from ctdose_core import __version__

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"
CITATION = REPO / "CITATION.cff"
ZENODO = REPO / ".zenodo.json"
#: The document that actually reaches the editor. The Section Editor reclassified this
#: submission, so the full Research Article was reformatted into a Technical Note and is
#: retained only for the separate Original Article -- and every check here went on
#: reading the Research Article, which is to say a file nobody submits. Both are checked
#: now, because both are still cited from somewhere.
TECHNOTE = REPO / "paper" / "ctdose-core_jacmp_technote.md"
MANUSCRIPT = REPO / "paper" / "ctdose-core_jacmp.md"
SUBMITTED = (TECHNOTE, MANUSCRIPT)

#: The organisation the manuscript's data-availability statement points a reader at.
#: `Institute-of-One` was confirmed to exist on GitHub; `lisit-ioo` returns 404.
REPO_URL = "https://github.com/Institute-of-One/ctdose-core"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _cff() -> dict:
    yaml = pytest.importorskip("yaml", reason="pyyaml not installed")
    return yaml.safe_load(CITATION.read_text(encoding="utf-8"))


def test_the_version_is_the_same_everywhere(pyproject):
    assert pyproject["project"]["version"] == __version__
    assert json.loads(ZENODO.read_text(encoding="utf-8"))["version"] == __version__
    assert str(_cff()["version"]) == __version__


def test_every_declared_url_points_at_the_repository_the_manuscript_cites(pyproject):
    urls = pyproject["project"]["urls"]
    assert urls["Homepage"] == REPO_URL
    for value in urls.values():
        assert value.startswith(REPO_URL), f"{value} is not under {REPO_URL}"

    cff = _cff()
    assert cff["repository-code"] == REPO_URL

    zenodo = json.loads(ZENODO.read_text(encoding="utf-8"))
    related = [r["identifier"] for r in zenodo["related_identifiers"]]
    assert REPO_URL in related

    for path in SUBMITTED:
        assert REPO_URL in path.read_text(encoding="utf-8"), (
            f"{path.name} does not point a reader at {REPO_URL}"
        )


def test_the_manuscript_names_no_other_github_organisation():
    """A second organisation in the text means one of the two citations is dead."""
    for path in SUBMITTED:
        text = path.read_text(encoding="utf-8")
        orgs = set(re.findall(r"github\.com/([A-Za-z0-9_.-]+)", text))
        assert orgs == {"Institute-of-One"}, (
            f"unexpected GitHub organisations in {path.name}: {orgs}"
        )


def test_the_manuscript_cites_the_version_doi_and_not_the_concept_doi():
    """The Zenodo badge shows the concept DOI. The paper has to cite the version.

    A concept DOI resolves to whichever release is newest, so a reader who follows it
    after the next tag is handed code that is not the code the results came from. The
    two DOIs differ by a few digits in the middle of a number nobody reads, which is
    exactly why this is asserted rather than looked at: the same substitution reached
    print in nineteen files across this programme before anyone noticed.
    """
    zenodo = json.loads(ZENODO.read_text(encoding="utf-8"))
    concept = next(
        related["identifier"]
        for related in zenodo["related_identifiers"]
        if related["relation"] == "isVersionOf"
    )
    for path in SUBMITTED:
        text = path.read_text(encoding="utf-8")
        cited = set(re.findall(r"10\.5281/zenodo\.\d+", text))
        assert cited, f"{path.name} cites no Zenodo archive at all"
        assert concept not in cited, (
            f"{path.name} cites the concept DOI {concept}, which follows the newest "
            f"release rather than the archived version the results came from"
        )
        assert f"version {__version__}" in text, (
            f"{path.name} cites a Zenodo archive without naming the version it pins; "
            f"the declared version is {__version__}"
        )


def test_the_licence_is_mit_in_every_declaration(pyproject):
    assert pyproject["project"]["license"]["text"] == "MIT"
    assert _cff()["license"] == "MIT"
    assert json.loads(ZENODO.read_text(encoding="utf-8"))["license"] == "MIT"


def test_the_paper_is_not_cited_as_published_before_it_is(pyproject):
    """`preferred-citation` must not read as a published article until it is one.

    A CFF entry with a journal and a year but no DOI is easily mistaken for a real
    reference by citation managers, so the status field has to say what it is.
    """
    citation = _cff()["preferred-citation"]
    if "doi" not in citation:
        assert citation.get("status") in {"submitted", "in-preparation", "in-press"}, (
            "preferred-citation has no DOI, so it must carry a status saying so"
        )


def test_no_imaging_is_declared_as_shipped(pyproject):
    """The sdist must not carry DICOM beyond the small regression fixture.

    The manuscript states that no imaging is redistributed. `data/` holds only the
    scrubbed dummy-patient fixture and is deliberately absent from the sdist include
    list; if it is ever added, this test is the reminder that the claim changes with it.
    """
    include = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert not any(entry.startswith("data") for entry in include), (
        "the sdist would ship imaging; the manuscript says none is redistributed"
    )
