"""Effective dose: route selection, conversion factors and tube-voltage scaling."""

from __future__ import annotations

import math

import pytest

from ctdose_core.effdose import (
    effective_dose,
    effective_dose_from_dlp,
    effective_dose_from_dlp_air,
)
from ctdose_core.provenance import Quantity, Source
from ctdose_core.tables import lookup_conversion, lookup_scanner

BODY = lookup_scanner("GE LightSpeed VCT (small BS)", "Chest", "Adults")
ADULT_CHEST_F = lookup_conversion("Chest", "Adults", "F")
ADULT_CHEST_M = lookup_conversion("Chest", "Adults", "M")


def test_dlp_air_route_multiplies_the_documented_terms():
    e = effective_dose_from_dlp_air(1000.0, BODY, ADULT_CHEST_F, kvp=120.0)
    assert e.value == pytest.approx(1000.0 * ADULT_CHEST_F.fmean * BODY.ct_factor)
    assert e.unit == "mSv"
    assert e.inputs["f_mean"] == ADULT_CHEST_F.fmean
    assert e.inputs["k_CT"] == BODY.ct_factor
    assert e.inputs["sex"] == "F"


def test_kfactor_route_multiplies_the_documented_terms():
    e = effective_dose_from_dlp(1000.0, BODY, ADULT_CHEST_F, kvp=120.0)
    assert e.value == pytest.approx(1000.0 * BODY.ct_factor * ADULT_CHEST_F.kfactor)
    assert "ICRP" in e.reference


def test_tube_voltage_scaling_is_the_product_of_both_corrections():
    at_120 = effective_dose_from_dlp_air(1000.0, BODY, ADULT_CHEST_F, kvp=120.0).value
    at_140 = effective_dose_from_dlp_air(1000.0, BODY, ADULT_CHEST_F, kvp=140.0).value
    expected = (140 / 120) ** 2 * math.sqrt(140 / 120)
    assert at_140 / at_120 == pytest.approx(expected)


def test_missing_kvp_skips_the_correction_and_says_so():
    e = effective_dose_from_dlp_air(1000.0, BODY, ADULT_CHEST_F, kvp=None)
    assert e.inputs["k_kV_CTDI"] == 1.0
    assert e.inputs["k_kV_organ"] == 1.0
    assert "skipped" in e.inputs["note"]


def test_female_conversion_gives_the_higher_chest_dose():
    female = effective_dose_from_dlp_air(1000.0, BODY, ADULT_CHEST_F, kvp=120.0).value
    male = effective_dose_from_dlp_air(1000.0, BODY, ADULT_CHEST_M, kvp=120.0).value
    assert female > male


def test_route_selection_follows_the_scanner_table():
    e = effective_dose(BODY, ADULT_CHEST_F, dlp_air=1000.0, dlp_mgycm=800.0, kvp=120.0)
    assert "DLP_air" in e.formula

    child_coef = lookup_scanner("Aquilion Prime (small BS)", "Chest", "5 year old")
    child_factors = lookup_conversion("Chest", "5 year old", "M")
    assert child_coef.effective_dose_route == "dlp_kfactor"
    e2 = effective_dose(child_coef, child_factors, dlp_air=1000.0, dlp_mgycm=800.0, kvp=120.0)
    assert "k" in e2.formula and "DLP_air" not in e2.formula


def test_route_falls_back_when_the_prescribed_factor_is_absent():
    """A paediatric size with only f_mean must not fail on the k-factor route."""
    coef = lookup_scanner("GE LightSpeed VCT (small BS)", "Chest", "Children7year old")
    factors = lookup_conversion("Chest", "Children7year old", "M")
    assert factors.kfactor is None
    e = effective_dose(
        coef, factors, dlp_air=1000.0, dlp_mgycm=800.0, kvp=120.0, route="dlp_kfactor"
    )
    assert "f_mean" in e.formula


def test_route_can_be_forced():
    e = effective_dose(
        BODY, ADULT_CHEST_F, dlp_air=1000.0, dlp_mgycm=800.0, kvp=120.0, route="dlp_kfactor"
    )
    assert e.inputs["k"] == ADULT_CHEST_F.kfactor


def test_missing_input_for_the_chosen_route_is_an_error():
    with pytest.raises(ValueError, match="needs dlp_air"):
        effective_dose(BODY, ADULT_CHEST_F, dlp_mgycm=800.0)
    child_coef = lookup_scanner("Aquilion Prime (small BS)", "Chest", "5 year old")
    child_factors = lookup_conversion("Chest", "5 year old", "M")
    with pytest.raises(ValueError, match="needs dlp_mgycm"):
        effective_dose(child_coef, child_factors, dlp_air=1000.0)


def test_dlp_provenance_is_carried_into_the_effective_dose():
    dlp = Quantity(800.0, "mGy*cm", Source.PHYSICS_FALLBACK)
    e = effective_dose_from_dlp(dlp, BODY, ADULT_CHEST_F, kvp=120.0)
    assert e.inputs["DLP_source"] == "physics_fallback"
