"""The shipped coefficients must stay open-licensed, and stay attributed.

The per-scanner coefficients were replaced because the previous ones followed CT-Expo:
the formulae are free to reimplement, but transcribed per-scanner *values* are a copyright
and database-right exposure, and a Zenodo DOI is permanent. These tests guard the replacement in both directions -- that the open
data keeps its attribution, and that the superseded material cannot creep back in.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OPEN_TABLE = REPO / "ctdose_core" / "data" / "scanner_ctdi_mirdct.json"
SOURCE_RECORD = REPO / "data" / "reference" / "mirdct_dinwiddie2026" / "SOURCE.md"

#: The file that carried the CT-Expo-derived per-scanner values. Its *values* were the
#: exposure -- a transcribed commercial scanner database -- which is what the open table
#: replaces.
SUPERSEDED_TABLE = REPO / "ctdose_core" / "data" / "scanner_coefficients.json"

#: Naming CT-Expo is not itself a problem, and a blanket string ban would be wrong:
#:
#: * the CT-Expo *methodology* is a published formulation, free to reimplement and proper
#:   to cite, so `geometry.py` and the manuscript may and should name it;
#: * the other shipped tables are published standards data, cited to TG-204 and ICRP-103.
#:
#: What must not recur is per-scanner CTDI *values* of that lineage being shipped. These
#: keys are the shape of that data, and finding them outside the open table is the defect.
SUPERSEDED_VALUE_KEYS = ("nctdi_w", "nctdi_air", "nctdi_air_expr")


def test_the_open_coefficients_carry_licence_and_citation():
    p = json.loads(OPEN_TABLE.read_text(encoding="utf-8"))["provenance"]
    assert p["license"] == "CC BY 4.0"
    assert p["doi"] == "10.1002/acm2.70473"
    assert "Dinwiddie" in p["citation"]
    assert "J Appl Clin Med Phys" in p["citation"]
    assert p["source_sha256"]


def test_the_source_record_documents_the_licence_position():
    text = SOURCE_RECORD.read_text(encoding="utf-8")
    assert "CC BY 4.0" in text
    assert "10.1002/acm2.70473" in text
    # The directory is third-party data inside an MIT repository; that must be stated,
    # because MIT does not extend to it.
    assert "not MIT" in text
    assert "sha-256" in text.lower() or "sha256" in text.lower()


def test_no_shipped_data_file_carries_per_scanner_values_of_the_superseded_lineage():
    """The exposure was transcribed per-scanner CTDI values, not the word "CT-Expo".

    The other shipped tables -- TG-204 conversion factors, ICRP-103 k-factors, the J-RIME
    DRLs -- are published reference data attributed to those standards, which raises no
    third-party question. This test therefore looks for the *shape* of the superseded
    data rather than banning a string.
    """
    offenders = []
    for path in (REPO / "ctdose_core" / "data").glob("*.json"):
        if path.name == "scanner_ctdi_mirdct.json":
            continue
        body = path.read_text(encoding="utf-8").lower()
        for key in SUPERSEDED_VALUE_KEYS:
            if f'"{key}"' in body:
                offenders.append(f"{path.name}: {key}")
    assert not offenders, (
        "per-scanner CTDI values of the superseded lineage are still shipped: " f"{offenders}"
    )


def test_the_superseded_coefficient_table_is_not_shipped():
    """The per-scanner coefficient table is gone from the package."""
    assert not SUPERSEDED_TABLE.exists()


def test_the_zenodo_record_credits_the_coefficient_source():
    """A derived archive must name the data it derives from."""
    zenodo = json.loads((REPO / ".zenodo.json").read_text(encoding="utf-8"))
    identifiers = [r["identifier"] for r in zenodo.get("related_identifiers", [])]
    assert "10.1002/acm2.70473" in identifiers, (
        "the Zenodo record must relate to the CC BY 4.0 coefficient source"
    )
