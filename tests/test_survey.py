"""Survey logic, tested offline against a stub archive.

The network is never touched here: :class:`StubClient` serves synthetic headers, so
the sampling, screening, aggregation and exclusion accounting are all exercised in CI.
The real archive run lives in ``tools/run_tcia_survey.py``.
"""

from __future__ import annotations

import pydicom
import pytest
from pydicom.dataset import Dataset

from ctdose_core.survey import (
    DEFAULT_REGION,
    VENDOR_QUERIES,
    FallbackCase,
    SeriesScreen,
    TciaClient,
    evaluate_fallback,
    region_for,
    run_survey,
    screen_series,
    summarise_fallback,
    summarise_screens,
)


def make_header(
    *,
    model: str = "LightSpeed VCT",
    manufacturer: str = "GE MEDICAL SYSTEMS",
    ctdivol: float | None = None,
    dose_comment: str | None = None,
    tube_current: int | None = 200,
    exposure_time: int | None = 800,
    collimation: float | None = 40.0,
    pitch: str | None = "0.984:1",
    thickness: float | None = 5.0,
    slice_location: float | None = -20.0,
) -> Dataset:
    ds = Dataset()
    ds.Manufacturer = manufacturer
    ds.ManufacturerModelName = model
    if ctdivol is not None:
        ds.add_new((0x0018, 0x9345), "FD", ctdivol)
    if dose_comment is not None:
        ds.add_new((0x0040, 0x0310), "LT", dose_comment)
    if tube_current is not None:
        ds.add_new((0x0018, 0x1151), "IS", tube_current)
    if exposure_time is not None:
        ds.add_new((0x0018, 0x1150), "IS", exposure_time)
    if collimation is not None:
        ds.add_new((0x0018, 0x9307), "FD", collimation)
    if pitch is not None:
        ds.add_new((0x0043, 0x1027), "SH", pitch)
    if thickness is not None:
        ds.add_new((0x0018, 0x0050), "DS", str(thickness))
    if slice_location is not None:
        ds.add_new((0x0020, 0x1041), "DS", str(slice_location))
    return ds


class StubClient(TciaClient):
    """A TciaClient that serves canned index entries and headers."""

    def __init__(self, index: dict[str, list[dict]], headers: dict[str, Dataset]):
        super().__init__(base_url="stub://")
        self.index = index
        self.headers = headers
        self.downloads: list[str] = []

    def series(self, *, modality: str = "CT", manufacturer: str | None = None) -> list[dict]:
        return list(self.index.get(manufacturer or "", []))

    def sop_instance_uids(self, series_uid: str) -> list[str]:
        return [f"{series_uid}.1", f"{series_uid}.2", f"{series_uid}.3"]

    def single_image(self, series_uid: str, sop_uid: str) -> pydicom.Dataset:
        if series_uid not in self.headers:
            raise KeyError(series_uid)
        return self.headers[series_uid]

    def download_series(self, series_uid: str, dest):  # pragma: no cover - not exercised
        self.downloads.append(series_uid)
        raise RuntimeError("stub does not serve pixel data")


def entry(uid: str, *, collection="COLL", model="LightSpeed VCT", manufacturer="GE MEDICAL SYSTEMS", n=100):
    return {
        "SeriesInstanceUID": uid,
        "Collection": collection,
        "ManufacturerModelName": model,
        "Manufacturer": manufacturer,
        "ImageCount": n,
        "LicenseName": "CC BY 3.0",
        "LicenseURI": "http://creativecommons.org/licenses/by/3.0/",
        "BodyPartExamined": "CHEST",
    }


# --- screening ----------------------------------------------------------------------------


def test_screen_records_every_dose_relevant_attribute():
    client = StubClient({}, {"S1": make_header(ctdivol=9.4)})
    screen = screen_series(entry("S1"), "GE", client=client)
    assert screen.has_ctdivol_tag
    assert screen.has_tube_current and screen.has_timing and screen.has_collimation
    assert screen.has_pitch and screen.has_slice_thickness and screen.has_slice_location
    assert screen.fallback_feasible
    assert screen.surveyable
    assert screen.licence == "CC BY 3.0"


def test_missing_ctdivol_is_still_surveyable_when_the_technique_is_described():
    client = StubClient({}, {"S1": make_header(ctdivol=None)})
    screen = screen_series(entry("S1"), "GE", client=client)
    assert not screen.has_ctdivol_tag
    assert screen.fallback_feasible
    assert screen.surveyable


def test_a_series_with_neither_is_not_surveyable():
    """No recorded dose and no usable technique: no method can recover it."""
    client = StubClient({}, {"S1": make_header(ctdivol=None, tube_current=None)})
    screen = screen_series(entry("S1"), "GE", client=client)
    assert not screen.surveyable
    assert not screen.fallback_feasible


def test_pitch_is_not_required_for_feasibility():
    """An axial acquisition legitimately has no pitch; it defaults to 1.0."""
    client = StubClient({}, {"S1": make_header(pitch=None)})
    screen = screen_series(entry("S1"), "GE", client=client)
    assert not screen.has_pitch
    assert screen.fallback_feasible


def test_model_resolution_states():
    client = StubClient(
        {},
        {
            "resolved": make_header(model="LightSpeed VCT"),
            "unknown": make_header(model="Sensation 64"),
        },
    )
    # Matching against the open table is exact, so a covered model is resolved outright
    # and there is no ambiguous state left to represent.
    assert screen_series(entry("resolved"), "GE", client=client).model_resolution == "resolved"
    # Sensation 64 is not in the open database; reporting it as unknown is what
    # makes the series unrecoverable rather than costed against another scanner.
    unknown = screen_series(entry("unknown"), "Siemens", client=client)
    assert unknown.model_resolution == "unknown"
    assert unknown.model_resolved is None


def test_screen_records_transport_errors_rather_than_raising():
    client = StubClient({}, {})
    screen = screen_series(entry("missing"), "GE", client=client)
    assert screen.error
    assert "KeyError" in screen.error


# --- aggregation ---------------------------------------------------------------------------


def _screens() -> list[SeriesScreen]:
    def s(vendor, ctdi, feasible, resolution="resolved", error=""):
        return SeriesScreen(
            vendor=vendor,
            manufacturer_raw=vendor,
            collection="COLL",
            series_uid=f"{vendor}-{ctdi}-{feasible}-{resolution}-{error}",
            model_name="m",
            n_images=100,
            licence="CC BY 3.0",
            has_ctdivol_tag=ctdi,
            has_tube_current=feasible,
            has_timing=feasible,
            has_collimation=feasible,
            has_slice_thickness=feasible,
            has_slice_location=feasible,
            model_resolution=resolution,
            error=error,
        )

    return [
        s("GE", False, True),
        s("GE", False, True),
        s("GE", False, False),
        s("GE", False, True, error="boom"),
        s("Siemens", True, True),
        s("Siemens", False, True, resolution="unknown"),
    ]


def test_absence_rates_exclude_unreadable_series_from_the_denominator():
    summary = summarise_screens(_screens())
    ge = summary["by_vendor"]["GE"]
    assert ge["n_series_screened"] == 4
    assert ge["n_series_readable"] == 3
    assert ge["n_errors"] == 1
    assert ge["ctdivol_absence_rate"] == pytest.approx(1.0)
    assert ge["fallback_feasibility_rate"] == pytest.approx(2 / 3)
    assert ge["surveyable_rate"] == pytest.approx(2 / 3)

    siemens = summary["by_vendor"]["Siemens"]
    assert siemens["ctdivol_absence_rate"] == pytest.approx(0.5)
    assert siemens["model_resolution"]["unknown"] == 1

    overall = summary["overall"]
    assert overall["n_series_readable"] == 5
    assert overall["with_ctdivol_tag"] == 1
    assert overall["ctdivol_absence_rate"] == pytest.approx(4 / 5)
    assert "CC BY 3.0" in overall["licences"]
    assert overall["n_series_without_licence"] == 0


def test_series_the_index_gives_no_licence_for_are_counted():
    """They cannot be cited without resolving the collection by hand, so they are named."""
    screens = _screens()
    screens[0].licence = ""
    summary = summarise_screens(screens)
    assert summary["overall"]["n_series_without_licence"] == 1
    assert summary["by_vendor"]["GE"]["n_series_without_licence"] == 1


def test_unmatched_models_are_counted_for_follow_up():
    screens = _screens()
    screens[-1].model_name = "Discovery CT750 HD"
    summary = summarise_screens(screens)
    assert summary["unmatched_models"]["Discovery CT750 HD"] == 1


def test_fallback_summary_reports_a_distribution_not_a_correction_factor():
    cases = [
        FallbackCase(
            vendor="Siemens",
            collection="C",
            series_uid=str(i),
            model_name="m",
            table_model="t",
            n_slices=100,
            recorded_ctdivol_mgy=10.0,
            estimated_ctdivol_mgy=10.0 * (1 + pct / 100),
            relative_error=pct / 100,
            percent_error=pct,
            region="Chest",
        )
        for i, pct in enumerate([-15.0, -5.0, 0.0, 10.0, 40.0])
    ]
    summary = summarise_fallback(cases)
    d = summary["overall"]
    assert d["n"] == 5
    assert d["median"] == pytest.approx(0.0)
    assert d["mean"] == pytest.approx(6.0)
    assert d["mean_absolute"] == pytest.approx(14.0)
    assert d["min"] == pytest.approx(-15.0) and d["max"] == pytest.approx(40.0)
    assert d["within_20_percent"] == pytest.approx(4 / 5)
    assert d["within_50_percent"] == pytest.approx(1.0)
    assert "not a single" in summary["note"]


def test_empty_fallback_summary_is_well_formed():
    assert summarise_fallback([])["overall"] == {"n": 0}


def _case(pct: float, *, collimation_measured: bool, uid: str) -> FallbackCase:
    return FallbackCase(
        vendor="Philips",
        collection="C",
        series_uid=uid,
        model_name="m",
        table_model="t",
        n_slices=100,
        recorded_ctdivol_mgy=10.0,
        estimated_ctdivol_mgy=10.0 * (1 + pct / 100),
        relative_error=pct / 100,
        percent_error=pct,
        region="Chest",
        collimation_measured=collimation_measured,
    )


def test_error_is_stratified_by_whether_collimation_was_recorded():
    """A missing-metadata problem must not be attributed to the dose model."""
    cases = [
        _case(2.0, collimation_measured=True, uid="a"),
        _case(-4.0, collimation_measured=True, uid="b"),
        _case(280.0, collimation_measured=False, uid="c"),
        _case(160.0, collimation_measured=False, uid="d"),
    ]
    summary = summarise_fallback(cases)
    strata = summary["by_collimation_provenance"]
    assert strata["measured"]["n"] == 2
    assert strata["measured"]["mean_absolute"] == pytest.approx(3.0)
    assert strata["reconstructed"]["n"] == 2
    assert strata["reconstructed"]["mean_absolute"] == pytest.approx(220.0)
    assert summary["n_collimation_measured"] == 2
    assert summary["n_collimation_reconstructed"] == 2
    assert "misattribute" in strata["note"]


def test_headline_stratum_isolates_the_dose_model_from_metadata_gaps():
    """Diagnostic acquisitions with recorded collimation -- the method's own accuracy."""
    localiser = _case(64000.0, collimation_measured=True, uid="loc")
    localiser.recorded_ctdivol_mgy = 0.05
    guessed = _case(180.0, collimation_measured=False, uid="guessed")
    clean = [
        _case(8.0, collimation_measured=True, uid="a"),
        _case(-12.0, collimation_measured=True, uid="b"),
        _case(25.0, collimation_measured=True, uid="c"),
    ]
    summary = summarise_fallback([localiser, guessed, *clean])
    d = summary["diagnostic_and_collimation_measured"]
    assert d["n"] == 3
    assert d["n_excluded"] == 2
    assert d["median"] == pytest.approx(8.0)
    assert d["within_50_percent"] == pytest.approx(1.0)
    assert "the one to quote" in d["note"]
    # The confound between vendor and collimation availability must be visible.
    assert summary["vendor_by_collimation_provenance"]["Philips"] == {
        "collimation_measured": 4,
        "collimation_reconstructed": 1,
    }


def test_non_diagnostic_acquisitions_are_reported_as_a_separate_stratum():
    """A localiser's recorded CTDIvol must not be allowed to define the headline error."""
    localiser = _case(64000.0, collimation_measured=True, uid="loc")
    localiser.recorded_ctdivol_mgy = 0.05
    diagnostic = [
        _case(5.0, collimation_measured=True, uid="a"),
        _case(-5.0, collimation_measured=True, uid="b"),
    ]
    summary = summarise_fallback([localiser, *diagnostic])
    assert summary["overall"]["n"] == 3
    d = summary["diagnostic_acquisitions_only"]
    assert d["n"] == 2
    assert d["n_excluded"] == 1
    assert d["mean_absolute"] == pytest.approx(5.0)
    assert d["threshold_mgy"] == 0.5
    assert "this stratum is the one to quote" in d["note"]


def test_duplicate_cases_are_flagged_as_non_independent():
    """Archives re-release the same acquisition; the effective n is the distinct count."""
    cases = [
        _case(50.0, collimation_measured=True, uid="a"),
        _case(50.0, collimation_measured=True, uid="b"),
        _case(10.0, collimation_measured=True, uid="c"),
    ]
    ind = summarise_fallback(cases)["independence"]
    assert ind["n_cases"] == 3
    assert ind["n_distinct_recorded_estimated_pairs"] == 2
    assert "not fully independent" in ind["note"]


def test_implausible_recorded_values_are_recognised():
    from ctdose_core.survey import implausible_recorded_ctdivol

    assert not implausible_recorded_ctdivol(9.4)
    assert not implausible_recorded_ctdivol(850.0)
    assert implausible_recorded_ctdivol(0.0)
    assert implausible_recorded_ctdivol(-886609159389184.0)
    assert implausible_recorded_ctdivol(1e9)


def test_a_corrupt_recorded_value_is_excluded_not_averaged_in(dose_taro_images, tmp_path):
    """A nonsense CTDIvol in a vendor header must not enter the error statistics."""
    import pydicom as pd

    from ctdose_core import load_series

    work = tmp_path / "series"
    work.mkdir()
    for f in sorted(dose_taro_images.glob("*.dcm")):
        ds = pd.dcmread(str(f))
        ds.add_new((0x0018, 0x9345), "FD", -886609159389184.0)
        ds.save_as(str(work / f.name), enforce_file_format=True)

    screen = SeriesScreen(
        vendor="Philips",
        manufacturer_raw="Philips",
        collection="C",
        series_uid="S",
        model_name="LightSpeed VCT",
        n_images=12,
        has_ctdivol_tag=True,
        model_resolution="resolved",
        model_resolved="GE LightSpeed VCT (small BS)",
    )
    assert evaluate_fallback(screen, load_series(work)) is None


# --- fallback evaluation ----------------------------------------------------------------


def test_evaluate_fallback_needs_a_recorded_value(dose_taro_images):
    from ctdose_core import load_series

    series = load_series(dose_taro_images)  # carries no CTDIvol
    screen = SeriesScreen(
        vendor="GE",
        manufacturer_raw="GE MEDICAL SYSTEMS",
        collection="C",
        series_uid="S",
        model_name="LightSpeed VCT",
        n_images=12,
        has_ctdivol_tag=False,
        model_resolution="ambiguous",
        model_resolved="GE LightSpeed VCT (small BS)|GE LightSpeed VCT (medium BS)",
    )
    assert evaluate_fallback(screen, series) is None


def _ambiguous_series(images, dest, *, scan_fov_mm: float | None):
    """A copy of the fixture with a recorded CTDIvol, and with or without a scan field.

    The scan field of view is what lets the bowtie be inferred, so a test about the
    median-across-candidates path has to remove it, and a test about the inference has to
    set it. Doing both from one helper keeps the two paths honestly distinguished.
    """
    import pydicom as pd

    dest.mkdir()
    for f in sorted(images.glob("*.dcm")):
        ds = pd.dcmread(str(f))
        ds.add_new((0x0018, 0x9345), "FD", 8.0)
        for tag in ((0x0018, 0x0090), (0x0018, 0x1100)):
            if tag in ds:
                del ds[tag]
        if scan_fov_mm is not None:
            ds.add_new((0x0018, 0x0090), "DS", scan_fov_mm)
        ds.save_as(str(dest / f.name), enforce_file_format=True)
    return dest


def test_region_defaults_to_the_body_branch():
    assert region_for("HEAD") == "Head"
    assert region_for("brain") == "Head"
    assert region_for("CHEST") == DEFAULT_REGION
    assert region_for("") == DEFAULT_REGION


# --- orchestration -------------------------------------------------------------------------


def test_run_survey_samples_across_collections_and_records_its_parameters():
    """One huge collection must not swamp the sample."""
    index = {
        "GE MEDICAL SYSTEMS": [entry(f"BIG-{i}", collection="BIG") for i in range(50)]
        + [entry("SMALL-1", collection="SMALL"), entry("SMALL-2", collection="SMALL")],
        "GE": [],
    }
    headers = {e["SeriesInstanceUID"]: make_header() for e in index["GE MEDICAL SYSTEMS"]}
    client = StubClient(index, headers)

    result = run_survey(
        vendors=["GE"],
        screen_per_vendor=4,
        fallback_per_vendor=0,
        client=client,
        progress=False,
    )
    assert len(result.screens) == 4
    collections = [s.collection for s in result.screens]
    assert collections.count("SMALL") == 2, "round-robin must reach the small collection"
    assert result.parameters["seed"] == 20260726
    assert result.parameters["sampling"].startswith("stratified")
    assert result.parameters["modality"] == "CT"


def test_run_survey_counts_its_exclusions():
    index = {
        "SIEMENS": [
            entry("A", manufacturer="SIEMENS", model="Sensation 64", n=100),
            entry("B", manufacturer="SIEMENS", model="Unknown Scanner 9000", n=100),
            entry("C", manufacturer="SIEMENS", model="Sensation 64", n=100),
        ]
    }
    headers = {
        "A": make_header(manufacturer="SIEMENS", model="Sensation 64", ctdivol=9.0),
        "B": make_header(manufacturer="SIEMENS", model="Unknown Scanner 9000", ctdivol=9.0),
        "C": make_header(manufacturer="SIEMENS", model="Sensation 64", ctdivol=None),
    }
    client = StubClient(index, headers)
    result = run_survey(
        vendors=["Siemens"], screen_per_vendor=3, fallback_per_vendor=0, client=client, progress=False
    )
    assert result.excluded["no_recorded_ctdivol"] == 1
    assert result.excluded["model_not_in_table"] == 2

    payload = result.to_dict()
    assert payload["tag_absence"]["by_vendor"]["Siemens"]["n_series_readable"] == 3
    assert payload["fallback_error"]["overall"] == {"n": 0}
    assert "TCIA Data Usage Policy" in payload["data_use_note"]


def test_vendor_queries_cover_the_four_families():
    assert set(VENDOR_QUERIES) == {"GE", "Siemens", "Canon/Toshiba", "Philips"}
    assert "Canon Medical Systems" in VENDOR_QUERIES["Canon/Toshiba"]
    assert "TOSHIBA" in VENDOR_QUERIES["Canon/Toshiba"]
