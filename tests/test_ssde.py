"""Closed-form validation of the size metrics and SSDE conversion.

Every assertion here is checkable by hand: either against the published TG-204 head
formula, or against a synthetic phantom whose water-equivalent diameter is known
analytically.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import water_cylinder

from ctdose_core.provenance import Source
from ctdose_core.segmentation import contour_metrics
from ctdose_core.ssde import (
    HEAD_A,
    HEAD_B,
    conversion_factor,
    effective_diameter,
    effective_diameter_from_measurement,
    head_conversion_factor,
    phantom_for,
    ssde,
    water_equivalent_diameter,
)
from ctdose_core.tables import ssde_table_quality_findings, ssde_tables


@pytest.mark.parametrize("diameter", [8.0, 12.0, 16.0, 20.5, 25.0])
def test_head_conversion_matches_the_published_closed_form(diameter):
    """SSDE/head = 1.9852 * exp(-0.0486 * D_water) -- AAPM TG-204 / Report 293."""
    expected = 1.9852 * math.exp(-0.0486 * diameter)
    assert head_conversion_factor(diameter).value == pytest.approx(expected, rel=1e-12)
    assert (HEAD_A, HEAD_B) == (1.9852, -0.0486)


def test_head_conversion_is_used_when_region_is_head():
    d = 17.0
    assert conversion_factor(d, 16, region="head").value == pytest.approx(
        head_conversion_factor(d).value
    )


@pytest.mark.parametrize("phantom", [16, 32])
def test_interpolation_reproduces_the_table_at_tabulated_diameters(phantom):
    """At the integer diameters the reference implementation supported, we must agree."""
    key = "etab16" if phantom == 16 else "etab32"
    for d, f in ssde_tables()[key]:
        assert conversion_factor(d, phantom).value == pytest.approx(f, rel=1e-12)


def test_interpolation_is_monotonic_and_bracketed():
    lo = conversion_factor(25.0, 32).value
    mid = conversion_factor(25.5, 32).value
    hi = conversion_factor(26.0, 32).value
    assert hi < mid < lo


def test_out_of_range_diameters_clamp_and_say_so():
    tab = np.asarray(ssde_tables()["etab32"], dtype=float)
    small = conversion_factor(2.0, 32)
    large = conversion_factor(90.0, 32)
    assert small.value == pytest.approx(tab[0, 1])
    assert large.value == pytest.approx(tab[-1, 1])
    assert any("clamped" in n for n in small.notes)
    assert any("clamped" in n for n in large.notes)


# --- water-equivalent diameter: exact analytic checks ---------------------------------


@pytest.mark.parametrize("radius_cm", [5.0, 10.0, 15.0])
def test_water_disc_gives_its_own_geometric_diameter(radius_cm):
    """A uniform 0 HU disc of radius r has, by definition, D_w = 2r."""
    area = math.pi * radius_cm**2
    assert water_equivalent_diameter(0.0, area).value == pytest.approx(2 * radius_cm, rel=1e-12)


def test_double_density_scales_the_diameter_by_root_two():
    """At +1000 HU the attenuation is twice water's, so D_w grows by sqrt(2)."""
    area = math.pi * 10.0**2
    water = water_equivalent_diameter(0.0, area).value
    dense = water_equivalent_diameter(1000.0, area).value
    assert dense / water == pytest.approx(math.sqrt(2.0), rel=1e-12)


@pytest.mark.parametrize("radius_cm", [7.5, 12.0, 16.0])
def test_end_to_end_contour_recovers_a_synthetic_phantom_diameter(radius_cm):
    """Segmentation + WED on a synthetic water cylinder must return 2r to within a pixel."""
    img, spacing = water_cylinder(radius_cm, hu=0.0)
    contour = contour_metrics(img, spacing)
    d_w = water_equivalent_diameter(contour.mean_hu, contour.area_cm2).value
    # Tolerance is one pixel of diameter: the disc is rasterised on a finite grid.
    assert d_w == pytest.approx(2 * radius_cm, abs=2 * spacing[0] / 10.0)


def test_contour_ignores_a_detached_table_like_object():
    """A separate high-attenuation object must not be counted as patient."""
    img, spacing = water_cylinder(10.0, hu=0.0)
    img[-40:-10, 100:400] = 200.0  # a couch rail, not touching the phantom
    contour = contour_metrics(img, spacing)
    assert contour.area_cm2 == pytest.approx(math.pi * 10.0**2, rel=0.02)


def test_lung_like_cavity_is_filled_before_measuring():
    """Interior gas belongs to the patient volume and must not be excluded from the ROI."""
    img, spacing = water_cylinder(12.0, hu=0.0)
    centre = img.shape[0] // 2
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
    cavity = ((yy - centre) ** 2 + (xx - centre) ** 2) <= (60**2)
    img[cavity] = -800.0
    contour = contour_metrics(img, spacing)
    assert contour.area_cm2 == pytest.approx(math.pi * 12.0**2, rel=0.02)
    # Mean HU drops because the cavity is inside the ROI, so D_w is smaller than 24 cm.
    assert contour.mean_hu < -100
    assert water_equivalent_diameter(contour.mean_hu, contour.area_cm2).value < 24.0


@pytest.mark.parametrize("dimension", ["ap", "lateral", "ap+lateral"])
@pytest.mark.parametrize("phantom", [16, 32])
def test_measured_dimension_lookup_agrees_with_the_conversion_table(dimension, phantom):
    """Going measurement -> effective diameter -> factor must match the shipped factor."""
    key = {"ap": "aptab", "lateral": "lattab", "ap+lateral": "aplattab"}[dimension]
    name = f"{key}{phantom}"
    etab = np.asarray(ssde_tables()[f"etab{phantom}"], dtype=float)
    known_bad = {f["measured_dimension_cm"] for f in ssde_table_quality_findings() if f["table"] == name}
    for measured, d_eff, factor in ssde_tables()[name]:
        if measured in known_bad or not etab[0, 0] <= d_eff <= etab[-1, 0]:
            continue
        d = effective_diameter_from_measurement(measured, dimension, phantom)
        assert conversion_factor(d.value, phantom).value == pytest.approx(factor, abs=0.02)


def test_measured_dimension_lookup_rejects_an_unknown_dimension():
    with pytest.raises(ValueError, match="dimension must be"):
        effective_diameter_from_measurement(25.0, "oblique")


def test_measured_dimension_lookup_reports_clamping():
    out = effective_diameter_from_measurement(200.0, "ap", 32)
    assert any("clamped" in n for n in out.notes)


def test_effective_diameter_is_the_geometric_mean():
    assert effective_diameter(20.0, 45.0).value == pytest.approx(30.0)
    with pytest.raises(ValueError):
        effective_diameter(0.0, 30.0)


# --- SSDE -------------------------------------------------------------------------------


def test_ssde_is_ctdivol_times_the_conversion_factor():
    result = ssde(10.0, 30.0, phantom=32)
    assert result.ssde.value == pytest.approx(10.0 * result.factor.value)
    assert result.ssde.unit == "mGy"
    assert result.ssde.source is Source.DERIVED
    assert result.ssde.inputs["D_cm"] == 30.0
    assert result.ssde.inputs["phantom_cm"] == 32


def test_ssde_provenance_records_where_the_ctdivol_came_from():
    from ctdose_core.provenance import Quantity

    recorded = Quantity(12.0, "mGy", Source.DICOM_TAG)
    assert ssde(recorded, 28.0).ssde.inputs["CTDIvol_source"] == "dicom_tag"
    estimated = Quantity(12.0, "mGy", Source.PHYSICS_FALLBACK)
    assert ssde(estimated, 28.0).ssde.inputs["CTDIvol_source"] == "physics_fallback"


def test_smaller_patients_receive_a_larger_size_correction():
    small = ssde(10.0, 20.0, phantom=32).ssde.value
    large = ssde(10.0, 40.0, phantom=32).ssde.value
    assert small > 10.0 > large


def test_phantom_selection_follows_the_reference_rules():
    assert phantom_for("Head") == 16
    assert phantom_for("Neck") == 16
    assert phantom_for("Chest") == 32
    assert phantom_for("Chest", 12.0) == 16  # infant body scan
    assert phantom_for("Chest", 30.0) == 32


def test_non_physical_attenuation_is_rejected():
    with pytest.raises(ValueError, match="non-physical"):
        water_equivalent_diameter(-1200.0, 100.0)
    with pytest.raises(ValueError):
        water_equivalent_diameter(0.0, 0.0)
