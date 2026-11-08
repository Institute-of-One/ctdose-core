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


class TestModelMatching:
    """Matching a DICOM model name to a tabulated one, without inventing equivalences.

    This is where a licensing fix could quietly become a fabrication: the open database
    covers 17 current scanners and an archive is full of older ones with similar names.
    Attributing one scanner's measured output to another would be worse than reporting
    the series as unrecoverable.
    """

    @pytest.mark.parametrize(
        ("manufacturer", "model", "expected"),
        [
            # The annex misspells two models; the alias absorbs it so the stored data can
            # stay verbatim.
            ("TOSHIBA", "Aquilion ONE", ("Canon", "Aquiliion ONE")),
            ("SIEMENS", "SOMATOM Definition Edge", ("Siemens", "Defintion Edge")),
            # Vendor renames and DICOM spelling variation.
            ("Canon Medical Systems", "Aquilion ONE Genesis", ("Canon", "Aquilion ONE Genesis")),
            ("GE MEDICAL SYSTEMS", "LightSpeed VCT", ("GE", "LightSpeed VCT")),
            ("GE MEDICAL SYSTEMS", "LightSpeed16", ("GE", "LightSpeed 16")),
            ("Philips", "iCT 256", ("Philips", "iCT 256 Slice")),
            ("SIEMENS", "SOMATOM Force", ("Siemens", "Somatom Force")),
            ("TOSHIBA", "Aquilion PRIME", ("Canon", "Prime")),
        ],
    )
    def test_known_scanners_resolve(self, manufacturer, model, expected):
        from ctdose_core.ctdi_table import resolve_model

        assert resolve_model(manufacturer, model) == expected

    @pytest.mark.parametrize(
        ("manufacturer", "model", "why"),
        [
            ("SIEMENS", "SOMATOM Definition AS+",
             "128-slice; the table's AS20 is a 20-row detector"),
            ("SIEMENS", "SOMATOM Definition AS", "same family, different configuration"),
            ("SIEMENS", "Sensation 16", "an earlier platform entirely"),
            ("Philips", "Brilliance 64", "not the iCT the table covers"),
            ("TOSHIBA", "Aquilion", "a family name, not a model"),
            ("GE MEDICAL SYSTEMS", "Discovery STE", "not tabulated"),
            ("Hitachi", "SCENARIA", "vendor outside the database"),
        ],
    )
    def test_near_misses_are_refused_rather_than_approximated(self, manufacturer, model, why):
        from ctdose_core.ctdi_table import CoefficientNotAvailable, covers, resolve_model

        assert not covers(manufacturer, model), f"{model} must not match: {why}"
        with pytest.raises(CoefficientNotAvailable):
            resolve_model(manufacturer, model)

    def test_every_alias_points_at_a_model_that_exists(self):
        """An alias naming a model the table lacks would fail only at lookup time."""
        from ctdose_core.ctdi_table import _MODEL_ALIASES

        available = set(model_names())
        for (vendor, key), target in _MODEL_ALIASES.items():
            assert (vendor, target) in available, (
                f"alias {key!r} points at {vendor} {target!r}, which is not in the table"
            )

    def test_a_missing_model_name_is_refused(self):
        from ctdose_core.ctdi_table import CoefficientNotAvailable, resolve_model

        with pytest.raises(CoefficientNotAvailable, match="no scanner model name"):
            resolve_model("SIEMENS", "")


def test_the_model_grid_is_reported_for_coverage_analysis():
    """The survey needs to say which scanners are covered and which are not."""
    grids = {m.model: m for m in models()}
    flash = grids["Definition Flash"]
    assert flash.vendor == "Siemens"
    assert flash.kvp_values(32, "medium", 38.4) == [70.0, 80.0, 100.0, 120.0, 140.0]


def test_the_rows_the_ge_validation_arm_reads_are_pinned():
    """Every cell the GE arm's assumption grid uses, at the value it has today.

    What this is: a regression test. A reviewer observed on 2026-11-08 that the arm's
    standalone recomputation checks the *keys* of its grid against an inventory but takes
    the coefficient *values* from the same output it is checking, so a misextracted or
    mislooked-up coefficient would pass every arithmetic check. These eighteen values
    close that at the project level.

    What this is not: a check against the published annex. The values below were read
    from the shipped data file, which `tools/extract_mirdct_coefficients.py` produced
    from the source spreadsheet whose sha256 the provenance block records. So this
    detects a silent change to the data or to the lookup; it does not re-verify the
    extraction. `test_an_exact_grid_point_returns_the_tabulated_value` is the one value
    quoted verbatim from the annex.
    """
    PINNED = {
        ("Optima 660", 32, "small"): {10.0: 9.27, 20.0: 8.14, 40.0: 7.61},
        ("Optima 660", 32, "large"): {10.0: 10.6, 20.0: 9.31, 40.0: 8.7},
        ("LightSpeed VCT", 32, "small"): {10.0: 8.36, 20.0: 7.45, 40.0: 6.96},
        ("LightSpeed VCT", 32, "medium"): {10.0: 9.87, 20.0: 8.8, 40.0: 8.22},
        ("LightSpeed VCT", 32, "large"): {10.0: 9.5, 20.0: 8.47, 40.0: 7.92},
        ("LightSpeed VCT", 16, "small"): {10.0: 19.08, 20.0: 17.01, 40.0: 15.9},
        ("LightSpeed VCT", 16, "medium"): {10.0: 21.51, 20.0: 19.18, 40.0: 17.92},
    }
    for (model, phantom, bowtie), widths in PINNED.items():
        for collimation, expected in widths.items():
            hit = lookup(
                "GE", model,
                kvp=120, collimation_mm=collimation, phantom_cm=phantom, bowtie=bowtie,
            )
            assert hit.nctdi_w_mgy_per_100mas == pytest.approx(expected), (
                f"GE {model} {phantom} cm {bowtie} {collimation:g} mm at 120 kVp is "
                f"{hit.nctdi_w_mgy_per_100mas} and was pinned at {expected}"
            )
            assert hit.exact, f"should be an exact grid point: {hit.assumptions}"
            assert hit.bowtie == bowtie
            assert hit.collimation_mm == collimation

    # And the filters tabulated per phantom must be exactly these, since the arm's
    # envelope is their product with the collimations: asking for one that is not there
    # resolves to the nearest and records the substitution, which is how the arm knows to
    # exclude that cell from the grid.
    absent = lookup(
        "GE", "LightSpeed VCT",
        kvp=120, collimation_mm=40.0, phantom_cm=16, bowtie="large",
    )
    assert absent.bowtie != "large", "the 16 cm head phantom has no large filter here"
    assert absent.assumptions, "a substituted filter must be recorded as an assumption"
