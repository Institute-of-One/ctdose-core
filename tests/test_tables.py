"""Integrity of the coefficient tables extracted from the MATLAB reference.

These are the tests that fail loudly if a re-extraction silently drops or corrupts a
constant.
"""

from __future__ import annotations

import numpy as np
import pytest

from ctdose_core.tables import (
    UnknownScannerError,
    _scanner_records,
    conversion_regions,
    drl_tables,
    lookup_conversion,
    lookup_scanner,
    scanner_models,
    ssde_table_quality_findings,
    ssde_tables,
)

#: The one physically implausible coefficient in the reference implementation.
#: If this set changes, either the MATLAB was fixed or the extractor broke.
KNOWN_FLAGGED_MODEL = "GE LightSpeed VCT (medium BS)"
N_KNOWN_FLAGGED_ROWS = 3


def test_expected_scanner_inventory():
    models = scanner_models()
    assert len(models) == 30
    for expected in (
        "GE LightSpeed VCT (small BS)",
        "Aquilion ONE Vision (medium BS)",
        "Definition AS series",
        "Brilliance 40/64",
    ):
        assert expected in models


def test_every_usable_row_is_complete():
    """A row the library will actually use must carry all the constants it needs."""
    for rec in _scanner_records():
        if not rec.available:
            continue
        assert rec.nctdi_w is not None, rec
        assert rec.ct_factor is not None, rec
        assert rec.beam_width_ref_mm is not None, rec
        assert rec.overbeam_dz_mm is not None, rec
        assert rec.overrange_m is not None, rec
        assert rec.overrange_b is not None, rec
        if rec.effective_dose_route == "dlpair_fmean":
            assert rec.nctdi_air is not None, rec


def test_coefficients_are_physically_plausible_except_the_known_row():
    flagged = [r for r in _scanner_records() if r.quality_flags]
    assert len(flagged) == N_KNOWN_FLAGGED_ROWS
    assert {m for r in flagged for m in r.models} == {KNOWN_FLAGGED_MODEL}

    for rec in _scanner_records():
        if not rec.available or rec.quality_flags:
            continue
        assert 0.0 < rec.nctdi_w < 0.5, rec
        if rec.nctdi_air is not None:
            # Free-in-air CTDI always exceeds the in-phantom weighted CTDI.
            assert rec.nctdi_w < rec.nctdi_air < 1.0, rec
        # k_CT is a scanner/mode correction and may slightly exceed unity.
        assert 0.4 <= rec.ct_factor <= 1.2, rec
        assert 0.0 < rec.beam_width_ref_mm <= 320.0, rec


def test_lookup_prefers_explicit_case_over_otherwise():
    """Head/neck must resolve to the head-mode row, not the body ``otherwise`` branch."""
    head = lookup_scanner("GE LightSpeed VCT (small BS)", "Head", "Adults")
    body = lookup_scanner("GE LightSpeed VCT (small BS)", "Chest", "Adults")
    assert head.is_head_mode
    assert not body.is_head_mode
    assert head.nctdi_w != body.nctdi_w


def test_trunk_literal_matches_the_body_branch():
    """The reference's legacy lowercase ``'trunk'`` case duplicates the body branch."""
    trunk = lookup_scanner("GE LightSpeed 16", "trunk", "Adults")
    body = lookup_scanner("GE LightSpeed 16", "Chest", "Adults")
    assert trunk.nctdi_w == body.nctdi_w
    assert trunk.nctdi_air == body.nctdi_air
    assert trunk.ct_factor == body.ct_factor


def test_unknown_model_and_no_data_branches_raise():
    with pytest.raises(UnknownScannerError, match="not in the coefficient table"):
        lookup_scanner("Imaginary Scanner 9000", "Chest", "Adults")
    with pytest.raises(UnknownScannerError, match="No Data"):
        lookup_scanner("GE LightSpeed VCT (large BS)", "Head", "Adults")


def test_pediatric_kfactor_rows_inherit_the_adult_ct_factor():
    child = lookup_scanner("Aquilion Prime (small BS)", "Chest", "5 year old")
    adult = lookup_scanner("Aquilion Prime (small BS)", "Chest", "Adults")
    assert child.effective_dose_route == "dlp_kfactor"
    assert child.ct_factor == adult.ct_factor
    assert child.ct_factor_inherited_from


def test_conversion_factors_cover_every_region_for_adults():
    for region in conversion_regions():
        for sex in ("M", "F"):
            factors = lookup_conversion(region, "Adults", sex)
            assert factors.fmean is not None or factors.kfactor is not None


def test_unknown_sex_falls_back_to_the_female_row():
    assert lookup_conversion("Chest", "Adults", "").sex == "F"
    assert lookup_conversion("Chest", "Adults", "O").sex == "F"
    assert lookup_conversion("Chest", "Adults", "M").sex == "M"
    # The female conversion factor is the more conservative of the two for chest CT.
    assert lookup_conversion("Chest", "Adults", "F").fmean > lookup_conversion(
        "Chest", "Adults", "M"
    ).fmean


@pytest.mark.parametrize("name", ["etab16", "etab32"])
def test_effective_diameter_tables_decrease_strictly_with_size(name):
    tab = np.asarray(ssde_tables()[name], dtype=float)
    diameters, factors = tab[:, 0], tab[:, 1]
    assert np.all(np.diff(diameters) > 0)
    assert np.all(np.diff(factors) < 0), f"{name} conversion factors must decrease with size"
    assert np.all(factors > 0)


@pytest.mark.parametrize(
    "name", ["aptab16", "aptab32", "lattab16", "lattab32", "aplattab16", "aplattab32"]
)
def test_measured_dimension_tables_are_non_increasing(name):
    """These are tabulated to two decimals, so adjacent large sizes may tie."""
    tab = np.asarray(ssde_tables()[name], dtype=float)
    measured, effective_diameter, factors = tab[:, 0], tab[:, 1], tab[:, 2]
    assert np.all(np.diff(measured) > 0)
    assert np.all(np.diff(effective_diameter) > 0)
    assert np.all(np.diff(factors) <= 0), f"{name} conversion factors must not increase with size"
    assert np.all(factors > 0)


@pytest.mark.parametrize(
    "name", ["aptab16", "aptab32", "lattab16", "lattab32", "aplattab16", "aplattab32"]
)
def test_measured_dimension_tables_agree_with_the_effective_diameter_table(name):
    """Cross-check: column 3 must equal etab* evaluated at column 2.

    This is an independent consistency check on the extraction. The two families of
    table were transcribed separately in the reference implementation, so agreement
    between them is evidence that neither was mangled.
    """
    phantom = 16 if name.endswith("16") else 32
    etab = np.asarray(ssde_tables()[f"etab{phantom}"], dtype=float)
    tab = np.asarray(ssde_tables()[name], dtype=float)
    known_bad = {
        (f["table"], f["measured_dimension_cm"]) for f in ssde_table_quality_findings()
    }
    for measured, d_eff, factor in tab:
        if d_eff < etab[0, 0] or d_eff > etab[-1, 0]:
            continue  # outside the etab range: extrapolated, not comparable
        if (name, measured) in known_bad:
            continue
        expected = float(np.interp(d_eff, etab[:, 0], etab[:, 1]))
        assert factor == pytest.approx(expected, abs=0.02), (name, d_eff, factor, expected)


def test_the_one_known_table_discrepancy_is_the_only_one():
    """A cross-check regression: exactly one row disagrees, and it is the known one."""
    findings = ssde_table_quality_findings()
    assert len(findings) == 1
    f = findings[0]
    assert f["table"] == "aplattab16"
    assert f["measured_dimension_cm"] == 44.0
    assert f["factor_in_source"] == 0.84
    assert f["factor_from_etab"] == pytest.approx(0.812, abs=1e-3)
    assert "unreliable" in f["note"]


def test_ap_plus_lateral_is_about_twice_the_effective_diameter():
    """Sanity check on the AP+lateral table's units: the sum tracks 2*D_eff."""
    tab = np.asarray(ssde_tables()["aplattab16"], dtype=float)
    ratio = tab[:, 0] / tab[:, 1]
    assert np.all((ratio > 1.7) & (ratio < 2.5))


@pytest.mark.parametrize("name", ["etab16", "etab32"])
def test_ssde_tables_follow_an_exponential_law(name):
    """TG-204 conversion factors are an exponential fit; verify the shipped table is one."""
    tab = np.asarray(ssde_tables()[name], dtype=float)
    d, f = tab[:, 0], tab[:, 1]
    slope, intercept = np.polyfit(d, np.log(f), 1)
    predicted = np.exp(intercept + slope * d)
    ss_res = float(np.sum((f - predicted) ** 2))
    ss_tot = float(np.sum((f - f.mean()) ** 2))
    assert 1.0 - ss_res / ss_tot > 0.999
    assert slope < 0


def test_16cm_phantom_factors_exceed_32cm_at_the_same_diameter():
    e16 = {int(d): f for d, f in ssde_tables()["etab16"]}
    e32 = {int(d): f for d, f in ssde_tables()["etab32"]}
    common = sorted(set(e16) & set(e32))
    assert common
    for d in common:
        assert e32[d] > e16[d], d


def test_drl_table_shape_and_the_flagged_cell():
    raw = drl_tables()
    assert raw["provenance"]["name"] == "Japan DRLs 2015"
    assert len(raw["adult"]) == 6
    assert len(raw["pediatric"]) == 9
    for row in raw["adult"] + raw["pediatric"]:
        assert row["ctdivol_mgy"] > 0
        assert row["dlp_mgycm"] > 0

    corrected = [r for r in raw["pediatric"] if "source_value_ctdivol_mgy" in r]
    assert len(corrected) == 1, "exactly one paediatric cell is a documented typo correction"
    row = corrected[0]
    assert row["key"] == "chest" and row["source_value_ctdivol_mgy"] == 141.0
    assert row["ctdivol_mgy"] == 14.0
    assert "Verify against" in row["data_quality_note"]


def test_pediatric_drls_increase_with_age():
    raw = drl_tables()
    for key in ("head", "chest", "abdomen"):
        rows = sorted(
            (r for r in raw["pediatric"] if r["key"] == key), key=lambda r: r["age_min_years"]
        )
        ctdi = [r["ctdivol_mgy"] for r in rows]
        dlp = [r["dlp_mgycm"] for r in rows]
        assert ctdi == sorted(ctdi), f"{key}: CTDIvol DRL should not decrease with age"
        assert dlp == sorted(dlp), f"{key}: DLP DRL should not decrease with age"
