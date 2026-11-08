"""Integrity of the coefficient tables extracted from the MATLAB reference.

These are the tests that fail loudly if a re-extraction silently drops or corrupts a
constant.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from ctdose_core.tables import (
    conversion_regions,
    drl_tables,
    lookup_conversion,
    ssde_table_quality_findings,
    ssde_tables,
)

#: The one physically implausible coefficient in the reference implementation.
#: If this set changes, either the MATLAB was fixed or the extractor broke.
KNOWN_FLAGGED_MODEL = "GE LightSpeed VCT (medium BS)"
N_KNOWN_FLAGGED_ROWS = 3


#: AAPM Report No. 96 (2008), Table 3, "Normalized effective dose per dose-length
#: product (DLP) for adults (standard physique) and pediatric patients of various ages
#: over various body regions", k in mSv mGy^-1 cm^-1. Transcribed from the report itself
#: (page 19) rather than from a secondary table, because the shipped file had credited
#: these values to ICRP Publication 103 -- which gives the effective-dose formalism and
#: tissue weighting factors, not DLP conversion coefficients -- and one value had been
#: mistranscribed, which nothing was checking.
AAPM_96_TABLE_3 = {
    "Head and Neck":  {"0 year old": 0.013, "1 year old": 0.0085, "5 year old": 0.0057,
                       "10 year old": 0.0042, "Adults": 0.0031},
    "Head":           {"0 year old": 0.011, "1 year old": 0.0067, "5 year old": 0.0040,
                       "10 year old": 0.0032, "Adults": 0.0021},
    "Neck":           {"0 year old": 0.017, "1 year old": 0.012, "5 year old": 0.011,
                       "10 year old": 0.0079, "Adults": 0.0059},
    "Chest":          {"0 year old": 0.039, "1 year old": 0.026, "5 year old": 0.018,
                       "10 year old": 0.013, "Adults": 0.014},
    "Whole abdomen":  {"0 year old": 0.049, "1 year old": 0.030, "5 year old": 0.020,
                       "10 year old": 0.015, "Adults": 0.015},
    "Trunk":          {"0 year old": 0.044, "1 year old": 0.028, "5 year old": 0.019,
                       "10 year old": 0.014, "Adults": 0.015},
}


@pytest.mark.parametrize("region", sorted(AAPM_96_TABLE_3))
def test_every_k_factor_matches_aapm_report_96_table_3(region):
    """The shipped k-factors must be the published ones, cell by cell.

    Head and neck at 1 year old was 0.0065 against Table 3's 0.0085, an error of -23.5%
    that survived because nothing compared the file with its source. It changed no
    released result -- the published cohort uses only the Chest and Head rows -- but
    nothing in the repository could have told you that.
    """
    for size, expected in AAPM_96_TABLE_3[region].items():
        for sex in ("M", "F"):
            got = lookup_conversion(region, size, sex).kfactor
            assert got == pytest.approx(expected, abs=1e-12), (
                f"{region} / {size} / {sex}: shipped {got}, AAPM Report 96 Table 3 "
                f"gives {expected}"
            )


def test_the_conversion_factors_name_their_real_sources_and_weighting():
    """Two different quantities from two different sources, each named with its weighting.

    ``kfactor`` is AAPM Report 96 Table 3, which rests on ICRP 60 tissue weighting via
    the European Guidelines and Shrimpton 2006. ``fmean`` is the ICRP 103 set from Deak
    et al. 2010. Crediting ICRP 103 for both, as the file did, misdescribes the
    effective doses the package reports, because ICRP 103 changed several weighting
    factors -- breast and the gonads most -- so the two are not interchangeable.
    """
    from ctdose_core.tables import conversion_provenance

    p = conversion_provenance()
    sources = " ".join(p["primary_sources"])
    assert "AAPM Report No. 96" in sources and "Table 3" in sources
    assert "Deak" in sources and "10.1148/radiol.10100047" in sources
    assert "ICRP 60" in p["tissue_weighting"]
    assert "ICRP Publication 103 (2007) set" in p["tissue_weighting"]
    assert "not interchangeable" in p["tissue_weighting"]
    # The superseded attributions must not come back. Scoped to the fields that describe
    # the data as it stands: the corrections log quotes the old wording on purpose, and a
    # check that forbade the word everywhere would forbid recording what was fixed.
    live = json.dumps({k: v for k, v in p.items() if k != "corrections"})
    assert "TG-204" not in live, (
        "Report 204 is the SSDE report and has nothing to do with DLP-to-effective-dose "
        "conversion"
    )
    assert "ICRP Publication 103" not in " ".join(p["primary_sources"]), (
        "ICRP 103 is not a source of DLP conversion coefficients; it belongs in "
        "tissue_weighting, describing what fmean rests on"
    )

    # The correction is on the record, with what it was, what it is now, and why.
    correction = next(
        c for c in p["corrections"]
        if c.get("field") == "kfactor" and c.get("region") == "Head and Neck"
    )
    assert correction["was"] == 0.0065 and correction["now"] == 0.0085
    assert "Table 3" in correction["source"]
    assert "No released result changes" in correction["why"], (
        "a correction must say whether any published number moved"
    )
    attribution = next(c for c in p["corrections"] if c.get("field") == "provenance")
    assert "TG-204" in attribution["was"], (
        "the corrections log is where the superseded attribution is recorded; if it no "
        "longer names what was wrong, the record has lost its point"
    )


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
    # The correction is no longer a suspicion: it was confirmed against the primary
    # source, so the note must carry the citation a reader can check rather than an
    # instruction to go and check it.
    assert row["verified"] is True
    note = row["data_quality_note"]
    assert "Verify against" not in note, "the caveat outlived its verification"
    assert "10.1007/s00247-015-3474-x" in note
    assert "14.3" in note and "296.0" in note, "the primary-source values must be quoted"


