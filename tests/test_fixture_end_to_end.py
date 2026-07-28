"""End-to-end on the bundled DOSE TARO fixture (real GE LightSpeed VCT data)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ctdose_core import analyze_series, load_series
from ctdose_core.io import iter_dicom_files
from ctdose_core.organ import organ_weights_from_masks
from ctdose_core.pipeline import measure_patient_size, patient_size_label
from ctdose_core.provenance import Source
from ctdose_core.scanners import match_models, resolve_model
from ctdose_core.tcia import inventory

pytestmark = pytest.mark.fixture_data


def test_fixture_carries_no_identifiers(dose_taro_images):
    """The published fixture must not carry institution or accession identifiers."""
    import pydicom

    for path in sorted(dose_taro_images.glob("*.dcm")):
        ds = pydicom.dcmread(str(path), stop_before_pixels=True)
        assert str(ds.PatientName) == "DOSE^TARO"
        assert str(ds.PatientID) == "CTDOSE-FIXTURE"
        for tag in ((0x0008, 0x0050), (0x0008, 0x0080), (0x0008, 0x1010), (0x0010, 0x0030)):
            assert not (tag in ds and str(ds[tag].value).strip())
        assert (0x0040, 0x0275) not in ds


def test_series_loads_with_the_expected_acquisition(dose_taro_images):
    series = load_series(dose_taro_images)
    assert series.n_slices == len(iter_dicom_files(dose_taro_images))
    assert series.manufacturer == "GE MEDICAL SYSTEMS"
    assert series.model_name == "LightSpeed VCT"
    assert series.kvp == 120.0
    assert series.slice_thickness_mm == 5.0
    assert series.pitch.value == pytest.approx(0.984)
    assert series.pitch.source is Source.DICOM_TEXT  # GE's '0.984:1' string
    assert series.collimation_mm.value == pytest.approx(40.0)
    assert series.rotation_time_s.value == pytest.approx(0.8)
    assert series.tube_current_ma.size == series.n_slices


def test_the_fixture_is_exactly_the_tag_absent_case_the_library_exists_for(dose_taro_images):
    """No CTDIvol, no DLP record, but a full tube-current profile."""
    series = load_series(dose_taro_images)
    inv = inventory(series)
    assert not inv.has_ctdivol_tag
    assert not inv.has_dlp_record
    assert inv.has_tube_current
    assert inv.tube_current_is_modulated


def test_model_matching_resolves_to_the_vct_family(dose_taro_images):
    """The open table names one LightSpeed VCT row, not a bowtie-split family.

    Bowtie is a lookup key in the open database rather than part of the model name, so
    there is a single row per scanner and nothing for the matcher to disambiguate.
    """
    series = load_series(dose_taro_images)
    candidates = match_models(series.model_name, series.manufacturer)
    assert all("LightSpeed VCT" in c.table_model for c in candidates[:1])
    assert resolve_model(series.model_name, series.manufacturer)[0] == "LightSpeed VCT"


def test_model_matching_never_crosses_vendors(dose_taro_images):
    """A Philips model must not be costed against a GE coefficient row."""
    from ctdose_core.scanners import table_model_vendor

    for name, manufacturer, vendor in (
        ("Mx8000 IDT 16", "Philips Medical Systems", "Philips"),
        ("Biograph64", "SIEMENS", "Siemens"),
        ("SOMATOM PLUS 4", "SIEMENS", "Siemens"),
        ("GEMINI TF TOF 16", "Philips", "Philips"),
    ):
        for candidate in match_models(name, manufacturer):
            assert table_model_vendor(candidate.table_model) in ("", vendor), (
                name,
                candidate.table_model,
            )


@pytest.mark.parametrize(
    "name,manufacturer,expected",
    [
        ("SOMATOM Definition Edge", "SIEMENS", "Defintion Edge"),
        ("SOMATOM Definition Flash", "SIEMENS", "Definition Flash"),
        ("iCT 256", "Philips", "iCT 256 Slice"),
        ("Discovery CT750 HD", "GE MEDICAL SYSTEMS", "Discovery CT750 HD"),
        ("LightSpeed VCT", "GE MEDICAL SYSTEMS", "LightSpeed VCT"),
        ("Aquilion PRIME", "TOSHIBA", "Prime"),
    ],
)
def test_real_archive_model_names_resolve_correctly(name, manufacturer, expected):
    """Model names taken verbatim from the public-archive survey.

    Several of these were *unresolvable* against the previous table and are covered by
    the open one -- Definition Flash, iCT 256 and Discovery CT750 HD among them. The
    expected string is the table's own spelling, typos included.
    """
    assert resolve_model(name, manufacturer)[0] == expected


@pytest.mark.parametrize(
    "name,manufacturer",
    [
        # A whole family name that does not identify a row.
        ("Aquilion", "TOSHIBA"),
        # Older scanners the open database does not cover. Reporting these as
        # unrecoverable is the point: the alternative is costing them against a
        # different scanner's measured output.
        ("Sensation 64", "SIEMENS"),
        ("Sensation 16", "SIEMENS"),
        ("SOMATOM Definition AS+", "SIEMENS"),
        ("Brilliance 64", "Philips"),
        ("Brilliance Big Bore", "Philips"),
        ("LightSpeed QX/i", "GE MEDICAL SYSTEMS"),
        ("Mx8000 IDT 16", "Philips Medical Systems"),
    ],
)
def test_weak_matches_are_reported_as_unknown_rather_than_guessed(name, manufacturer):
    """Costing a scanner against the wrong coefficient row is worse than saying so."""
    with pytest.raises(LookupError):
        resolve_model(name, manufacturer)


def test_water_equivalent_diameter_is_physiologically_plausible(dose_taro_images):
    """A 41 kg, 1.46 m adult chest-pelvis scan: D_w should sit in the small-adult range."""
    series = load_series(dose_taro_images)
    diameter, detail = measure_patient_size(series)
    assert diameter is not None
    assert diameter.source is Source.DERIVED
    assert 15.0 < diameter.value < 32.0
    assert len(detail["per_slice_dw_cm"]) >= 3
    for value in detail["per_slice_dw_cm"]:
        assert 12.0 < value < 40.0
    for area in detail["area_cm2"]:
        assert 200.0 < area < 1500.0
    for ap, lat in zip(detail["ap_cm"], detail["lateral_cm"], strict=True):
        assert lat > ap  # a supine trunk is wider than it is deep


def test_full_analysis_produces_an_auditable_report(dose_taro_images):
    report = analyze_series(
        dose_taro_images,
        region="Chest",
        scanner_model="GE LightSpeed VCT (small BS)",
        drl_key="routine_chest",
    )
    assert report.patient_size == "Adults"
    assert report.ctdivol.source is Source.PHYSICS_FALLBACK
    assert report.ctdivol.value > 0
    assert report.dlp.value > 0
    assert report.ssde is not None
    assert report.effective_dose is not None

    # SSDE exceeds CTDIvol for this small patient.
    assert report.ssde.value > report.ctdivol.value

    # The reconstruction is disclosed rather than presented as a recorded value.
    codes = {a.code for a in report.alerts}
    assert "provenance.ctdivol" in codes

    assert len(report.drl_comparisons) == 2
    payload = json.loads(json.dumps(report.to_dict(), default=str))
    assert payload["quantities"]["ctdivol"]["formula"]
    assert "Dinwiddie" in payload["coefficients"]["citation"]
    assert payload["coefficients"]["license"] == "CC BY 4.0"
    assert payload["alert_summary"]["n"] == len(report.alerts)


def test_forcing_the_fallback_is_a_no_op_when_nothing_was_recorded(dose_taro_images):
    a = analyze_series(
        dose_taro_images, region="Chest", scanner_model="GE LightSpeed VCT (small BS)",
        measure_size=False,
    )
    b = analyze_series(
        dose_taro_images, region="Chest", scanner_model="GE LightSpeed VCT (small BS)",
        measure_size=False, prefer_recorded=False,
    )
    assert a.ctdivol.value == pytest.approx(b.ctdivol.value)


def test_organ_weighting_uses_the_per_slice_tube_current(dose_taro_images):
    """A mask over the highest-mA slices must outweigh one over the lowest."""
    series = load_series(dose_taro_images)
    n = series.n_slices
    ma = series.tube_current_ma
    order = np.argsort(ma)
    low, high = order[: n // 3], order[-(n // 3) :]

    masks = {
        "low_current_region": np.array([1000 if i in low else 0 for i in range(n)]),
        "high_current_region": np.array([1000 if i in high else 0 for i in range(n)]),
    }
    profile = organ_weights_from_masks(masks, ma, ctdivol_mgy=7.5)
    weights = profile.as_mapping()
    assert weights["high_current_region"] > weights["low_current_region"]
    assert profile.weights[0].organ == "high_current_region"
    assert profile.weights[0].relative_dose_index_mgy == pytest.approx(
        7.5 * weights["high_current_region"]
    )
    assert any("not an absolute organ dose" in n for n in profile.notes)


def test_patient_size_label_from_age():
    assert patient_size_label(70) == "Adults"
    assert patient_size_label(None) == "Adults"
    assert patient_size_label(0.1) == "Children8week old"
    assert patient_size_label(7) == "Children7year old"
    assert patient_size_label(5, prefer_kfactor=True) == "5 year old"
