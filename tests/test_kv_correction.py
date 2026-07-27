"""Tube-voltage correction on the physics-fallback CTDIvol.

The scanner's tabulated nCTDIw refers to its reference tube voltage (120 kVp). A
model-neutral reconstruction of CTDIvol for a scan acquired at a different voltage
must scale by (kVp/kV_ref)^2, or it overestimates the as-delivered CTDIvol -- by ~46 %
for a 100 kVp abdomen scan, validated against a vendor Source-of-Truth (TCIA L277:
recorded CTDIvol 10.12 mGy; uncorrected fallback 14.78 mGy; corrected 10.26 mGy).
"""

from __future__ import annotations

import pytest

from ctdose_core.metrics import Acquisition, estimate_ctdivol
from ctdose_core.provenance import Source
from ctdose_core.tables import lookup_scanner


def _acq(kvp: float | None) -> Acquisition:
    return Acquisition(
        mas_per_rotation=200.0,
        collimation_mm=38.4,
        pitch=1.0,
        slice_thickness_mm=3.0,
        scan_range_mm=300.0,
        kvp=kvp,
    )


@pytest.fixture()
def coef():
    return lookup_scanner("Definition AS series", "Whole abdomen", "Adults")


def test_correction_scales_by_kv_ratio_squared(coef):
    acq = _acq(100.0)
    base = estimate_ctdivol(acq, coef, kv_correction=False).value
    corrected = estimate_ctdivol(acq, coef, kv_correction=True).value
    assert corrected / base == pytest.approx((100.0 / coef.kv_reference) ** 2)


def test_default_is_corrected(coef):
    acq = _acq(100.0)
    assert estimate_ctdivol(acq, coef).value == pytest.approx(
        estimate_ctdivol(acq, coef, kv_correction=True).value
    )


def test_no_change_at_reference_voltage(coef):
    acq = _acq(coef.kv_reference)
    assert estimate_ctdivol(acq, coef, kv_correction=True).value == pytest.approx(
        estimate_ctdivol(acq, coef, kv_correction=False).value
    )


def test_no_change_when_kvp_unknown(coef):
    acq = _acq(None)
    assert estimate_ctdivol(acq, coef, kv_correction=True).value == pytest.approx(
        estimate_ctdivol(acq, coef, kv_correction=False).value
    )


def test_provenance_records_the_correction(coef):
    q = estimate_ctdivol(_acq(100.0), coef, kv_correction=True)
    assert q.source is Source.PHYSICS_FALLBACK
    assert "kVp/kV_ref" in q.formula
    assert q.inputs["k_kV"] == pytest.approx((100.0 / coef.kv_reference) ** 2)
    assert any("tube-voltage corrected" in n for n in q.notes)
