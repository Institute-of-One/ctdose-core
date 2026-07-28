"""Dose metrics: the recorded-vs-fallback contract, and the geometry corrections."""

from __future__ import annotations

import pytest

from ctdose_core.metrics import (
    Acquisition,
    fallback_error,
)


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


# --- CTDIvol ------------------------------------------------------------------------------


# --- DLP --------------------------------------------------------------------------------


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
