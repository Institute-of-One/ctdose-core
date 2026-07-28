"""Effective dose by the published k-factor route.

The route lost its per-scanner terms with the coefficient table: it was
``DLP * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5 * k``, of which ``k_CT`` was per-scanner
and both tube-voltage terms were tied to a per-scanner reference voltage. What remains
depends only on the examination and the patient, which is what makes it attributable to
ICRP Publication 103 alone.
"""

from __future__ import annotations

import dataclasses

import pytest

from ctdose_core.effdose import effective_dose
from ctdose_core.metrics import estimate_dlp_open
from ctdose_core.provenance import Quantity, Source
from ctdose_core.tables import lookup_conversion

ADULT_CHEST_F = lookup_conversion("Chest", "Adults", "F")
ADULT_CHEST_M = lookup_conversion("Chest", "Adults", "M")


def test_effective_dose_is_the_product_of_dlp_and_the_published_k_factor():
    e = effective_dose(500.0, ADULT_CHEST_F)
    assert float(e) == pytest.approx(500.0 * ADULT_CHEST_F.kfactor)
    assert e.unit == "mSv"
    assert e.source is Source.DERIVED
    assert e.formula == "E = DLP * k(region, size)"


def test_no_per_scanner_term_survives_in_the_inputs():
    """The inputs are the audit trail; a scanner-conditioned term would show here."""
    e = effective_dose(500.0, ADULT_CHEST_F)
    assert set(e.inputs) == {"DLP_mGycm", "DLP_source", "k", "region", "size", "sex"}
    for gone in ("k_CT", "k_kV_CTDI", "k_kV_organ", "kV_ref"):
        assert gone not in e.inputs


def test_the_conversion_factor_is_sex_specific_for_the_chest():
    assert ADULT_CHEST_F.kfactor >= ADULT_CHEST_M.kfactor


def test_a_missing_k_factor_is_refused_rather_than_defaulted():
    # dataclasses.replace, not object.__setattr__: lookup_conversion hands back a shared
    # cached row, so mutating it in place leaks into every later test in the session.
    factors = dataclasses.replace(lookup_conversion("Chest", "Adults", "F"), kfactor=None)
    with pytest.raises(ValueError, match="no k-factor"):
        effective_dose(500.0, factors)


def test_the_dlp_source_is_carried_through():
    recorded = Quantity(value=500.0, unit="mGy*cm", source=Source.RDSR)
    assert effective_dose(recorded, ADULT_CHEST_F).inputs["DLP_source"] == "rdsr"


def test_a_reconstructed_dlp_carries_its_over_ranging_caveat_into_the_effective_dose():
    """The caveat must reach E, not stop at the DLP it came from."""
    dlp = estimate_dlp_open(10.0, 300.0)
    e = effective_dose(dlp, ADULT_CHEST_F)
    assert any("over-ranging" in n for n in e.notes)
    assert any("not an individual patient's dose" in n for n in e.notes)
    assert e.inputs["DLP_source"] == Source.DERIVED.value
