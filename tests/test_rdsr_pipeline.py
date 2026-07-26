"""RDSR wired into the pipeline: source priority, event matching, cross-checking.

The CT Radiation Dose SR objects used here are *synthesised* by
:func:`make_rdsr`, so these tests need no third-party sample data and run in CI.
Parsing of real vendor objects is covered separately by ``test_rdsr.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from ctdose_core import analyze_series, load_series
from ctdose_core.metrics import SOURCE_PRIORITY, most_authoritative
from ctdose_core.pipeline import collect_recorded_dose
from ctdose_core.provenance import Quantity, Source
from ctdose_core.rdsr import find_rdsr, match_acquisition, phantom_cm, read_rdsr

pytestmark = pytest.mark.fixture_data

ENHANCED_SR_STORAGE = "1.2.840.10008.5.1.4.1.1.88.22"


def _code(value: str, meaning: str, scheme: str = "DCM") -> Dataset:
    ds = Dataset()
    ds.CodeValue = value
    ds.CodingSchemeDesignator = scheme
    ds.CodeMeaning = meaning
    return ds


def _container(code: str, meaning: str, children: list[Dataset]) -> Dataset:
    ds = Dataset()
    ds.RelationshipType = "CONTAINS"
    ds.ValueType = "CONTAINER"
    ds.ConceptNameCodeSequence = [_code(code, meaning)]
    ds.ContinuityOfContent = "SEPARATE"
    ds.ContentSequence = children
    return ds


def _num(code: str, meaning: str, value: float, unit: str, unit_meaning: str) -> Dataset:
    mv = Dataset()
    mv.NumericValue = str(value)
    mv.MeasurementUnitsCodeSequence = [_code(unit, unit_meaning, scheme="UCUM")]
    ds = Dataset()
    ds.RelationshipType = "CONTAINS"
    ds.ValueType = "NUM"
    ds.ConceptNameCodeSequence = [_code(code, meaning)]
    ds.MeasuredValueSequence = [mv]
    return ds


def _text(code: str, meaning: str, value: str) -> Dataset:
    ds = Dataset()
    ds.RelationshipType = "CONTAINS"
    ds.ValueType = "TEXT"
    ds.ConceptNameCodeSequence = [_code(code, meaning)]
    ds.TextValue = value
    return ds


def _coded(code: str, meaning: str, value_code: str, value_meaning: str) -> Dataset:
    ds = Dataset()
    ds.RelationshipType = "CONTAINS"
    ds.ValueType = "CODE"
    ds.ConceptNameCodeSequence = [_code(code, meaning)]
    ds.ConceptCodeSequence = [_code(value_code, value_meaning)]
    return ds


def _event(
    *, protocol: str, ctdivol: float, dlp: float, phantom: str, scanning_length_mm: float
) -> Dataset:
    dose = _container(
        "113829",
        "CT Dose",
        [
            _num("113830", "Mean CTDIvol", ctdivol, "mGy", "mGy"),
            _coded("113835", "CTDIw Phantom Type", "113690", phantom),
            _num("113838", "DLP", dlp, "mGy.cm", "mGy.cm"),
        ],
    )
    params = _container(
        "113822",
        "CT Acquisition Parameters",
        [
            _num("113825", "Scanning Length", scanning_length_mm, "mm", "mm"),
            _num("113827", "Nominal Total Collimation Width", 40.0, "mm", "mm"),
            _num("113828", "Pitch Factor", 0.984, "{ratio}", "ratio"),
        ],
    )
    return _container(
        "113819",
        "CT Acquisition",
        [_text("125203", "Acquisition Protocol", protocol), params, dose],
    )


def make_rdsr(
    path: Path,
    *,
    events: list[Dataset],
    total_dlp: float,
    study_uid: str,
    manufacturer: str = "GE MEDICAL SYSTEMS",
    model: str = "LightSpeed VCT",
) -> Path:
    """Write a minimal but standard-shaped CT Radiation Dose SR."""
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = ENHANCED_SR_STORAGE
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = ENHANCED_SR_STORAGE
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "SR"
    ds.Manufacturer = manufacturer
    ds.ManufacturerModelName = model
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.StudyDate = "20000101"
    ds.PatientName = "DOSE^TARO"
    ds.PatientID = "CTDOSE-FIXTURE"
    ds.ValueType = "CONTAINER"
    ds.ConceptNameCodeSequence = [_code("113701", "X-Ray Radiation Dose Report")]
    ds.ContinuityOfContent = "SEPARATE"
    accumulated = _container(
        "113811",
        "CT Accumulated Dose Data",
        [
            _num("113812", "Total Number of Irradiation Events", len(events), "{events}", "events"),
            _num("113813", "CT Dose Length Product Total", total_dlp, "mGy.cm", "mGy.cm"),
        ],
    )
    ds.ContentSequence = [accumulated, *events]
    ds.save_as(str(path), enforce_file_format=True)
    return path


@pytest.fixture
def study_dir(dose_taro_images, tmp_path) -> Path:
    """A study directory: the image series plus room for a sibling dose SR."""
    series_dir = tmp_path / "study" / "series"
    series_dir.mkdir(parents=True)
    for f in sorted(dose_taro_images.glob("*.dcm")):
        shutil.copy(f, series_dir / f.name)
    return series_dir


def _study_uid(series_dir: Path) -> str:
    return load_series(series_dir).study_instance_uid


# --- source priority --------------------------------------------------------------------


def test_rdsr_outranks_the_image_header_and_the_fallback():
    assert SOURCE_PRIORITY.index(Source.RDSR) < SOURCE_PRIORITY.index(Source.DICOM_TAG)
    assert SOURCE_PRIORITY.index(Source.DICOM_TAG) < SOURCE_PRIORITY.index(Source.DICOM_TEXT)
    assert SOURCE_PRIORITY.index(Source.DICOM_TEXT) < SOURCE_PRIORITY.index(Source.PHYSICS_FALLBACK)

    tag = Quantity(9.0, "mGy", Source.DICOM_TAG)
    sr = Quantity(9.4, "mGy", Source.RDSR)
    guess = Quantity(7.6, "mGy", Source.PHYSICS_FALLBACK)
    assert most_authoritative([tag, sr, guess]) is sr
    assert most_authoritative([tag, guess]) is tag
    assert most_authoritative([]) is None


def test_phantom_code_maps_to_a_diameter():
    assert phantom_cm("IEC Head Dosimetry Phantom") == 16
    assert phantom_cm("IEC Body Dosimetry Phantom") == 32
    assert phantom_cm("") is None


# --- discovery and matching ---------------------------------------------------------------


def test_sr_beside_the_series_is_found_and_used(study_dir):
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=712.0,
        study_uid=uid,
    )
    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert report.rdsr is not None
    assert report.ctdivol.source is Source.RDSR
    assert report.ctdivol.value == pytest.approx(9.42)
    assert report.dlp.source is Source.RDSR
    assert report.dlp.value == pytest.approx(712.0)
    assert report.ctdivol.inputs["phantom_cm"] == 32
    assert report.rdsr_match.confidence == "exact"


def test_sr_from_another_study_is_ignored(study_dir):
    make_rdsr(
        study_dir.parent / "other_study.dcm",
        events=[
            _event(
                protocol="whatever",
                ctdivol=99.0,
                dlp=9999.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=9999.0,
        study_uid=generate_uid(),
    )
    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert report.rdsr is None
    assert report.ctdivol.source is Source.PHYSICS_FALLBACK


def test_multi_event_sr_matches_on_protocol_name(study_dir):
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="Localiser",
                ctdivol=0.14,
                dlp=6.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=700.0,
            ),
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            ),
        ],
        total_dlp=718.0,
        study_uid=uid,
    )
    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert report.rdsr_match.method == "protocol name"
    assert report.ctdivol.value == pytest.approx(9.42)


def test_an_unattributable_event_falls_back_rather_than_guessing(study_dir):
    """Two indistinguishable events must not be assigned arbitrarily."""
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="Phase 1",
                ctdivol=9.4,
                dlp=700.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            ),
            _event(
                protocol="Phase 2",
                ctdivol=18.8,
                dlp=1400.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            ),
        ],
        total_dlp=2100.0,
        study_uid=uid,
    )
    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert report.rdsr is not None
    assert report.rdsr_match.confidence == "ambiguous"
    assert report.rdsr_match.candidates == 2
    assert report.ctdivol.source is Source.PHYSICS_FALLBACK
    assert any("not attributed" in w or "no irradiation event" in w for w in report.warnings)


def test_scanning_length_breaks_a_protocol_tie(study_dir):
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="",
                ctdivol=0.2,
                dlp=8.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=120.0,
            ),
            _event(
                protocol="",
                ctdivol=9.4,
                dlp=700.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=655.0,
            ),
        ],
        total_dlp=708.0,
        study_uid=uid,
    )
    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert report.rdsr_match.confidence == "probable"
    assert report.ctdivol.value == pytest.approx(9.4)
    assert any("irradiated length" in n for n in report.rdsr_match.notes)


# --- keeping and comparing both sources ------------------------------------------------


def test_both_sources_are_kept_and_compared(study_dir):
    """When the header and the SR disagree, both are reported plus the difference."""
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=712.0,
        study_uid=uid,
    )
    # Give the image header a CTDIvol that disagrees with the SR.
    for f in sorted(study_dir.glob("*.dcm")):
        ds = pydicom.dcmread(str(f))
        ds.add_new((0x0018, 0x9345), "FD", 8.00)
        ds.save_as(str(f), enforce_file_format=True)

    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert report.ctdivol.source is Source.RDSR
    assert len(report.recorded_candidates["ctdivol"]) == 2
    sources = {q.source for q in report.recorded_candidates["ctdivol"]}
    assert sources == {Source.RDSR, Source.DICOM_TAG}

    cmp = report.source_agreement["ctdivol"]
    assert cmp["a"]["source"] == "rdsr" and cmp["a"]["value"] == pytest.approx(9.42)
    assert cmp["b"]["source"] == "dicom_tag" and cmp["b"]["value"] == pytest.approx(8.00)
    assert cmp["percent_difference"] == pytest.approx(100 * (8.00 - 9.42) / 9.42)
    assert any("differ by" in w for w in report.warnings)

    payload = report.to_dict()
    assert payload["source_agreement"]["ctdivol"]["absolute_difference"] < 0
    assert len(payload["recorded_candidates"]["ctdivol"]) == 2


def test_agreement_within_tolerance_raises_no_warning(study_dir):
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=712.0,
        study_uid=uid,
    )
    for f in sorted(study_dir.glob("*.dcm")):
        ds = pydicom.dcmread(str(f))
        ds.add_new((0x0018, 0x9345), "FD", 9.43)
        ds.save_as(str(f), enforce_file_format=True)

    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    assert "ctdivol" in report.source_agreement
    assert not any("differ by" in w for w in report.warnings)


def test_use_rdsr_false_ignores_the_report(study_dir):
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=712.0,
        study_uid=uid,
    )
    report = analyze_series(
        study_dir,
        region="Chest",
        scanner_model="GE LightSpeed VCT (small BS)",
        measure_size=False,
        use_rdsr=False,
    )
    assert report.rdsr is None
    assert report.ctdivol.source is Source.PHYSICS_FALLBACK


def test_explicit_rdsr_path_bypasses_the_search(study_dir, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sr = make_rdsr(
        elsewhere / "dose.dcm",
        events=[
            _event(
                protocol="anything",
                ctdivol=12.5,
                dlp=900.0,
                phantom="IEC Head Dosimetry Phantom",
                scanning_length_mm=200.0,
            )
        ],
        total_dlp=900.0,
        study_uid=generate_uid(),
    )
    series = load_series(study_dir)
    recorded = collect_recorded_dose(series, rdsr_path=sr)
    assert recorded.ctdivol is not None
    assert recorded.ctdivol.source is Source.RDSR
    assert recorded.phantom_cm == 16


def test_head_phantom_from_the_sr_overrides_the_region_guess(study_dir):
    """A body-region scan reported against the 16 cm phantom must use that table."""
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Head Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=712.0,
        study_uid=uid,
    )
    report = analyze_series(
        study_dir, region="Chest", scanner_model="GE LightSpeed VCT (small BS)"
    )
    assert report.ssde_result.phantom_cm == 16


def test_forcing_the_fallback_still_ignores_a_recorded_sr(study_dir):
    """prefer_recorded=False is how the fallback is validated against recorded truth."""
    uid = _study_uid(study_dir)
    make_rdsr(
        study_dir.parent / "dose_report.dcm",
        events=[
            _event(
                protocol="6.8 Chest-Pelvis 5mm (FF)",
                ctdivol=9.42,
                dlp=712.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=660.0,
            )
        ],
        total_dlp=712.0,
        study_uid=uid,
    )
    kwargs = dict(
        region="Chest", scanner_model="GE LightSpeed VCT (small BS)", measure_size=False
    )
    recorded = analyze_series(study_dir, **kwargs)
    estimated = analyze_series(study_dir, prefer_recorded=False, **kwargs)
    assert recorded.ctdivol.source is Source.RDSR
    assert estimated.ctdivol.source is Source.PHYSICS_FALLBACK
    # Both are still recorded in the report, so the comparison is auditable.
    assert estimated.recorded_candidates["ctdivol"][0].source is Source.RDSR


# --- discovery helpers -------------------------------------------------------------------


def test_find_rdsr_filters_by_study(study_dir):
    uid = _study_uid(study_dir)
    ev = [
        _event(
            protocol="p",
            ctdivol=1.0,
            dlp=10.0,
            phantom="IEC Body Dosimetry Phantom",
            scanning_length_mm=100.0,
        )
    ]
    make_rdsr(study_dir.parent / "mine.dcm", events=ev, total_dlp=10.0, study_uid=uid)
    make_rdsr(study_dir.parent / "theirs.dcm", events=ev, total_dlp=10.0, study_uid=generate_uid())
    assert len(find_rdsr(study_dir)) == 2
    assert len(find_rdsr(study_dir, study_instance_uid=uid)) == 1


def test_find_rdsr_skips_non_dicom_files(tmp_path):
    (tmp_path / "README.md").write_text("not dicom", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("also not dicom", encoding="utf-8")
    assert find_rdsr(tmp_path, search_parent=False) == []


def test_parent_is_searched_only_when_the_series_directory_has_none(study_dir):
    """Widening to the study directory must not happen when a local SR exists."""
    uid = _study_uid(study_dir)
    ev = [
        _event(
            protocol="p",
            ctdivol=1.0,
            dlp=10.0,
            phantom="IEC Body Dosimetry Phantom",
            scanning_length_mm=100.0,
        )
    ]
    make_rdsr(study_dir.parent / "study_level.dcm", events=ev, total_dlp=10.0, study_uid=uid)
    assert len(find_rdsr(study_dir)) == 1  # widened to the parent

    make_rdsr(study_dir / "series_level.dcm", events=ev, total_dlp=10.0, study_uid=uid)
    found = find_rdsr(study_dir)
    assert len(found) == 1
    assert found[0].path.name == "series_level.dcm"


def test_summed_event_dlp_and_phantom_types(tmp_path):
    path = make_rdsr(
        tmp_path / "sr.dcm",
        events=[
            _event(
                protocol="a",
                ctdivol=1.0,
                dlp=100.0,
                phantom="IEC Head Dosimetry Phantom",
                scanning_length_mm=100.0,
            ),
            _event(
                protocol="b",
                ctdivol=2.0,
                dlp=250.0,
                phantom="IEC Body Dosimetry Phantom",
                scanning_length_mm=200.0,
            ),
        ],
        total_dlp=350.0,
        study_uid=generate_uid(),
    )
    report = read_rdsr(path)
    assert report.summed_event_dlp == pytest.approx(350.0)
    assert float(report.total_dlp) == pytest.approx(350.0)
    assert report.phantom_types == (
        "IEC Body Dosimetry Phantom",
        "IEC Head Dosimetry Phantom",
    )


def test_match_returns_none_when_no_event_carries_a_dose(tmp_path):
    path = make_rdsr(tmp_path / "sr.dcm", events=[], total_dlp=0.0, study_uid=generate_uid())
    report = read_rdsr(path)
    match = match_acquisition(report)
    assert match.acquisition is None
    assert match.confidence == "none"
