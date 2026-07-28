"""The open CTDI database, and the assumptions the lookup is allowed to make.

The coefficients are third-party CC BY 4.0 data (Dinwiddie et al. 2026). Two classes of
defect matter here: a lookup that silently returns the wrong row, and a lookup that
quietly invents a value for a technique nobody measured. Both are checked.
"""

from __future__ import annotations

import json

import pytest

from ctdose_core.ctdi_table import (
    BOWTIES,
    CoefficientNotAvailable,
    CtdiLookup,
    lookup,
    model_names,
    models,
    provenance,
)


def test_the_shipped_coefficients_carry_their_licence_and_citation():
    """Third-party data must not lose its attribution by being repackaged."""
    p = provenance()
    assert p["license"] == "CC BY 4.0"
    assert p["doi"] == "10.1002/acm2.70473"
    assert "Dinwiddie" in p["citation"]
    assert p["source_sha256"], "the source file digest must be recorded"
    # The unit is the thing most easily got wrong downstream, so it is explicit.
    assert p["quantity"] == "CTDIw"
    assert "100 mAs" in p["unit"] and "pitch 1.0" in p["unit"]


def test_the_database_has_the_published_shape():
    """17 models across 4 vendors, as the paper reports."""
    assert len(model_names()) == 17
    vendors = {v for v, _ in model_names()}
    assert vendors == {"Canon", "GE", "Philips", "Siemens"}


def test_an_exact_grid_point_returns_the_tabulated_value():
    """A published row, quoted verbatim from the annex."""
    r = lookup(
        "Canon", "Aquilion ONE Genesis",
        kvp=120, collimation_mm=10, phantom_cm=16, bowtie="small",
    )
    assert r.nctdi_w_mgy_per_100mas == pytest.approx(18.446601941747574)
    assert r.exact, f"an exact grid point should carry no assumptions: {r.assumptions}"


def test_every_row_is_reachable_and_returned_unchanged():
    """No row is shadowed by the key resolution -- the whole grid round-trips."""
    from ctdose_core.ctdi_table import DATA

    rows = json.loads(DATA.read_text(encoding="utf-8"))["rows"]
    for row in rows:
        r = lookup(
            row["vendor"], row["model"],
            kvp=row["kvp"], collimation_mm=row["collimation_mm"],
            phantom_cm=row["phantom_cm"], bowtie=row["bowtie"],
        )
        assert r.nctdi_w_mgy_per_100mas == pytest.approx(row["nctdi_w_mgy_per_100mas"]), (
            f"{row['vendor']} {row['model']} {row['kvp']}kVp did not round-trip"
        )
        assert r.exact


def test_an_uncovered_model_is_refused_not_approximated():
    """The scanner behind the manuscript's own validation case is not in this table.

    SOMATOM Definition AS+ is a 128-slice scanner; the table's nearest Siemens entry is
    AS20, whose collimations (12/14.4/19.2 mm) are those of a 20-row detector. Silently
    substituting one for the other would be a fabricated measurement, so the lookup
    refuses and the caller reports the series as unrecoverable.
    """
    with pytest.raises(CoefficientNotAvailable, match="not in the open coefficient database"):
        lookup("Siemens", "SOMATOM Definition AS+",
               kvp=120, collimation_mm=38.4, phantom_cm=32)


def test_tube_potential_outside_the_measured_range_is_refused():
    with pytest.raises(CoefficientNotAvailable, match="outside the tabulated range"):
        lookup("GE", "LightSpeed VCT", kvp=200, collimation_mm=20, phantom_cm=32)


def test_interpolated_tube_potential_is_bracketed_and_declared():
    """An untabulated kV is interpolated between measured points, never extrapolated."""
    lo = lookup("Canon", "Aquilion ONE Genesis",
                kvp=100, collimation_mm=10, phantom_cm=16, bowtie="small")
    hi = lookup("Canon", "Aquilion ONE Genesis",
                kvp=120, collimation_mm=10, phantom_cm=16, bowtie="small")
    mid = lookup("Canon", "Aquilion ONE Genesis",
                 kvp=110, collimation_mm=10, phantom_cm=16, bowtie="small")

    assert lo.nctdi_w_mgy_per_100mas < mid.nctdi_w_mgy_per_100mas < hi.nctdi_w_mgy_per_100mas
    assert not mid.exact
    assert any("interpolated between" in a for a in mid.assumptions)
    # The exponent recovered from the data should be physically plausible: CTDI output
    # rises roughly as kV^2.5-3 over this range.
    import math

    exponent = math.log(hi.nctdi_w_mgy_per_100mas / lo.nctdi_w_mgy_per_100mas) / math.log(120 / 100)
    assert 2.0 < exponent < 3.5, f"implausible tube-output exponent {exponent:.2f}"


def test_an_untabulated_collimation_snaps_to_the_nearest_and_says_so():
    r = lookup("Siemens", "Definition Flash",
               kvp=120, collimation_mm=23.0, phantom_cm=32, bowtie="medium")
    assert r.collimation_mm == 19.2
    assert any("nearest measured value" in a for a in r.assumptions)
    assert not r.exact


def test_an_unrecorded_bowtie_is_defaulted_by_phantom_and_declared():
    body = lookup("Siemens", "Definition Flash", kvp=120, collimation_mm=38.4, phantom_cm=32)
    assert body.bowtie == "medium", "the 32 cm phantom's conventional filter is tabulated here"
    assert any("bowtie not recorded" in a for a in body.assumptions)


def test_the_ragged_grid_is_resolved_against_what_exists_not_what_is_conventional():
    """Several (model, phantom) pairs were measured with only one bowtie.

    Definition Flash at 16 cm has only the large filter, so the conventional 'small' for a
    head phantom cannot be honoured. The lookup must pick what exists and say that is what
    it did, rather than reporting the conventional choice it did not make.
    """
    head = lookup("Siemens", "Definition Flash", kvp=120, collimation_mm=38.4, phantom_cm=16)
    assert head.bowtie == "large"
    note = " ".join(head.assumptions)
    assert "is not tabulated for this model" in note
    assert "available: large" in note
    assert "used the 'small' filter" not in note, "the note must not claim an unmade choice"


def test_every_lookup_result_is_self_describing():
    """Provenance is only useful if it survives to the caller."""
    r = lookup("GE", "Revolution CT ES", kvp=120, collimation_mm=40, phantom_cm=32)
    assert isinstance(r, CtdiLookup)
    assert r.vendor == "GE" and r.model == "Revolution CT ES"
    assert r.nctdi_w_mgy_per_100mas > 0
    assert r.bowtie in BOWTIES
    assert r.phantom_cm == 32


def test_the_model_grid_is_reported_for_coverage_analysis():
    """The survey needs to say which scanners are covered and which are not."""
    grids = {m.model: m for m in models()}
    flash = grids["Definition Flash"]
    assert flash.vendor == "Siemens"
    assert flash.kvp_values(32, "medium", 38.4) == [70.0, 80.0, 100.0, 120.0, 140.0]
