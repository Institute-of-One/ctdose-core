"""Dose metrics: the recorded-vs-fallback contract, and the geometry corrections."""

from __future__ import annotations

import math

import pytest

from ctdose_core.geometry import kv_corrections, overbeaming_factor, overranging_length
from ctdose_core.metrics import (
    Acquisition,
    ctdivol,
    dlp,
    dose_metrics,
    estimate_ctdivol,
    fallback_error,
    irradiated_length_cm,
)
from ctdose_core.provenance import Source
from ctdose_core.tables import lookup_scanner

COEF = lookup_scanner("GE LightSpeed VCT (small BS)", "Chest", "Adults")


def make_acq(**kw) -> Acquisition:
    base = dict(
        mas_per_rotation=100.0,
        collimation_mm=40.0,
        pitch=1.0,
        slice_thickness_mm=5.0,
        scan_range_mm=300.0,
        kvp=120.0,
    )
    base.update(kw)
    return Acquisition(**base)


# --- geometry ---------------------------------------------------------------------------


def test_overbeaming_is_unity_at_the_reference_collimation():
    assert overbeaming_factor(40.0, 40.0, 4.0).value == pytest.approx(1.0)


def test_overbeaming_penalises_narrow_collimation():
    wide = overbeaming_factor(40.0, 40.0, 4.0).value
    narrow = overbeaming_factor(10.0, 40.0, 4.0).value
    assert narrow > wide == 1.0
    assert narrow == pytest.approx((40 * 14) / (10 * 44))


def test_overranging_grows_with_pitch():
    lo = overranging_length(5.0, 40.0, 0.5, 0.5, 0.7).value
    hi = overranging_length(5.0, 40.0, 1.5, 0.5, 0.7).value
    assert hi > lo
    assert lo == pytest.approx(5.0 / 2 + 40.0 * (0.5 * 0.5 + 0.7))


def test_kv_corrections_are_unity_at_the_reference_voltage():
    k_ctdi, k_organ = kv_corrections(120.0, 120.0)
    assert k_ctdi.value == pytest.approx(1.0)
    assert k_organ.value == pytest.approx(1.0)


def test_kv_corrections_scale_as_documented():
    k_ctdi, k_organ = kv_corrections(140.0, 120.0)
    assert k_ctdi.value == pytest.approx((140 / 120) ** 2)
    assert k_organ.value == pytest.approx(math.sqrt(140 / 120))


# --- CTDIvol ------------------------------------------------------------------------------


def test_recorded_ctdivol_wins_and_is_labelled_as_recorded():
    q = ctdivol(make_acq(), COEF, recorded_mgy=9.5)
    assert q.value == 9.5
    assert q.source is Source.DICOM_TAG
    assert not q.is_estimated


def test_fallback_is_used_when_no_value_was_recorded():
    q = ctdivol(make_acq(), COEF, recorded_mgy=None)
    assert q.source is Source.PHYSICS_FALLBACK
    assert q.is_estimated
    assert "0018,9345" in q.notes[0]
    assert q.inputs["nCTDIw_mGy_per_mAs"] == COEF.nctdi_w


def test_fallback_can_be_forced_even_when_a_value_was_recorded():
    """This is how the fallback is validated: run it on series that do carry the tag."""
    acq = make_acq()
    forced = ctdivol(acq, COEF, recorded_mgy=9.5, prefer_recorded=False)
    assert forced.source is Source.PHYSICS_FALLBACK
    assert forced.value == pytest.approx(estimate_ctdivol(acq, COEF).value)


def test_ctdivol_scales_linearly_with_mas_and_inversely_with_pitch():
    base = estimate_ctdivol(make_acq(), COEF).value
    doubled = estimate_ctdivol(make_acq(mas_per_rotation=200.0), COEF).value
    halved_pitch = estimate_ctdivol(make_acq(pitch=0.5), COEF).value
    assert doubled == pytest.approx(2 * base)
    assert halved_pitch == pytest.approx(2 * base)


# --- DLP --------------------------------------------------------------------------------


def test_dlp_includes_overranging():
    acq = make_acq()
    length = irradiated_length_cm(acq, COEF)
    assert length.value > acq.scan_range_mm / 10.0
    assert length.inputs["L_rot_mm"] > 0

    c = estimate_ctdivol(acq, COEF)
    d = dlp(acq, COEF, c)
    assert d.value == pytest.approx(c.value * length.value)
    assert "over-ranging" in d.notes[0]


def test_dlp_carries_the_ctdivol_provenance_forward():
    acq = make_acq()
    recorded = ctdivol(acq, COEF, recorded_mgy=9.5)
    assert dlp(acq, COEF, recorded).inputs["CTDIvol_source"] == "dicom_tag"
    estimated = ctdivol(acq, COEF)
    assert dlp(acq, COEF, estimated).inputs["CTDIvol_source"] == "physics_fallback"


def test_recorded_dlp_wins():
    acq = make_acq()
    d = dlp(acq, COEF, 10.0, recorded_mgycm=777.0)
    assert d.value == 777.0
    assert d.source is Source.DICOM_TEXT


def test_dose_metrics_bundles_all_three():
    m = dose_metrics(make_acq(), COEF)
    assert set(m) == {"ctdivol", "dlp", "dlp_air"}
    assert m["dlp_air"].value > m["dlp"].value  # free-in-air exceeds in-phantom


# --- fallback validation ------------------------------------------------------------------


def test_fallback_error_reports_signed_relative_error():
    err = fallback_error(recorded=10.0, estimated=11.0)
    assert err["absolute_error"] == pytest.approx(1.0)
    assert err["relative_error"] == pytest.approx(0.1)
    assert err["percent_error"] == pytest.approx(10.0)
    under = fallback_error(recorded=10.0, estimated=8.0)
    assert under["percent_error"] == pytest.approx(-20.0)
    with pytest.raises(ValueError):
        fallback_error(recorded=0.0, estimated=1.0)


# --- validation of inputs -------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs", [{"mas_per_rotation": 0}, {"pitch": -1}, {"collimation_mm": 0}, {"scan_range_mm": -5}]
)
def test_invalid_acquisition_is_rejected(kwargs):
    with pytest.raises(ValueError):
        make_acq(**kwargs)
