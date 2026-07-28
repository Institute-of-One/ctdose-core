"""The committed results must be internally consistent with themselves.

`results/*.json` are the numbers the manuscript cites. These tests guard against a
stale or hand-edited file: the summary blocks must agree with the per-row data they
claim to summarise, and every file must carry its provenance. They skip when the file
is absent, so a fresh clone that has not run the tools still passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RESULTS = Path(__file__).resolve().parents[1] / "results"


def _load(name: str) -> dict:
    path = RESULTS / name
    if not path.exists():
        pytest.skip(f"{path} not generated; run the corresponding script in tools/")
    return json.loads(path.read_text(encoding="utf-8"))


# --- Layer 1 -------------------------------------------------------------------------


def test_layer1_summary_agrees_with_its_rows():
    payload = _load("rdsr_layer1.json")
    summary, files = payload["summary"], payload["files"]
    parsed = [f for f in files if f.get("is_ct_rdsr")]

    assert summary["files_seen"] == len(files)
    assert summary["files_parsed_as_ct_rdsr"] == len(parsed)
    assert summary["files_rejected"] == len(files) - len(parsed)
    assert summary["total_irradiation_events"] == sum(f["n_events_parsed"] for f in parsed)
    assert summary["events_with_ctdivol"] == sum(f["n_events_with_ctdivol"] for f in parsed)
    assert summary["vendors"] == sorted({f["vendor"] for f in parsed})


def test_layer1_event_rows_match_the_per_file_counts():
    payload = _load("rdsr_layer1.json")
    events = payload["events"]
    for f in payload["files"]:
        if not f.get("is_ct_rdsr"):
            continue
        rows = [e for e in events if e["file"] == f["file"]]
        assert len(rows) == f["n_events_parsed"], f["file"]
        assert sum(1 for e in rows if e["ctdivol_mgy"] is not None) == f["n_events_with_ctdivol"]


def test_layer1_dlp_sum_check_is_reproducible_from_the_rows():
    payload = _load("rdsr_layer1.json")
    for f in payload["files"]:
        check = f.get("dlp_sum_vs_total") or {}
        if not check.get("comparable"):
            continue
        rows = [e for e in payload["events"] if e["file"] == f["file"]]
        summed = sum(e["dlp_mgycm"] for e in rows if e["dlp_mgycm"] is not None)
        assert summed == pytest.approx(check["summed_event_dlp_mgycm"]), f["file"]


def test_layer1_covers_four_vendors_and_both_phantoms():
    """The claim the Layer-1 table exists to support."""
    summary = _load("rdsr_layer1.json")["summary"]
    assert len(summary["vendors"]) == 4
    assert set(summary["phantoms_observed"]) == {16, 32}
    check = summary["dlp_sum_check"]
    assert check["files_comparable"] > 0
    assert check["files_within_tolerance"] == check["files_comparable"]
    counts = summary["event_count_check"]
    assert counts["files_matching"] == counts["files_declaring_a_count"]


def test_layer1_attributes_its_third_party_source():
    provenance = _load("rdsr_layer1.json")["provenance"]
    assert "OpenREM" in provenance["sample_source"]
    assert "GPL" in provenance["sample_source"]
    assert provenance["generated_by"].endswith("make_rdsr_layer1.py")


# --- Layer 2 -------------------------------------------------------------------------


def test_survey_absence_rates_agree_with_the_series_rows():
    payload = _load("survey.json")
    rows = payload["series"]
    for vendor, block in payload["tag_absence"]["by_vendor"].items():
        mine = [r for r in rows if r["vendor"] == vendor]
        readable = [r for r in mine if not r["error"]]
        assert block["n_series_screened"] == len(mine), vendor
        assert block["n_series_readable"] == len(readable), vendor
        with_ctdi = sum(1 for r in readable if r["has_ctdivol_tag"])
        assert block["with_ctdivol_tag"] == with_ctdi, vendor
        if readable:
            expected = (len(readable) - with_ctdi) / len(readable)
            assert block["ctdivol_absence_rate"] == pytest.approx(expected), vendor


def test_survey_fallback_cases_are_all_present_in_the_screened_set():
    payload = _load("survey.json")
    screened = {r["series_uid"] for r in payload["series"]}
    for case in payload["fallback_cases"]:
        assert case["series_uid"] in screened


def test_survey_percent_error_is_reproducible_from_the_two_values():
    payload = _load("survey.json")
    for case in payload["fallback_cases"]:
        recorded = case["recorded_ctdivol_mgy"]
        estimated = case["estimated_ctdivol_mgy"]
        expected = 100.0 * (estimated - recorded) / recorded
        assert case["percent_error"] == pytest.approx(expected), case["series_uid"]


def test_survey_only_kept_physically_plausible_recorded_values():
    from ctdose_core.survey import implausible_recorded_ctdivol

    for case in _load("survey.json")["fallback_cases"]:
        assert not implausible_recorded_ctdivol(case["recorded_ctdivol_mgy"])


def test_survey_strata_partition_the_cases():
    payload = _load("survey.json")
    fb = payload["fallback_error"]
    cases = payload["fallback_cases"]
    strata = fb["by_collimation_provenance"]
    assert strata["measured"]["n"] + strata["reconstructed"]["n"] == len(cases)
    assert fb["n_collimation_measured"] == sum(1 for c in cases if c["collimation_measured"])
    assert fb["overall"]["n"] == len(cases)
    # ``by_vendor`` is the per-manufacturer case count -- the breakdown the manuscript
    # quotes -- so it must account for every evaluated case exactly once.
    assert sum(fb["by_vendor"].values()) == len(cases)
    for vendor, n in fb["by_vendor"].items():
        assert n == sum(1 for c in cases if c["vendor"] == vendor)

    for vendor, counts in fb["vendor_by_collimation_provenance"].items():
        mine = [c for c in cases if c["vendor"] == vendor]
        assert counts["collimation_measured"] == sum(1 for c in mine if c["collimation_measured"])
        assert counts["collimation_reconstructed"] == sum(
            1 for c in mine if not c["collimation_measured"]
        )


def test_survey_headline_stratum_is_reproducible_from_the_rows():
    """The number the manuscript quotes must be recomputable from the shipped cases."""
    from ctdose_core.survey import DIAGNOSTIC_CTDIVOL_MGY

    payload = _load("survey.json")
    cases = payload["fallback_cases"]
    headline = payload["fallback_error"]["diagnostic_and_collimation_measured"]

    expected = [
        c
        for c in cases
        if c["collimation_measured"] and c["recorded_ctdivol_mgy"] >= DIAGNOSTIC_CTDIVOL_MGY
    ]
    assert headline["n"] == len(expected)
    assert headline["n_excluded"] == len(cases) - len(expected)
    if expected:
        errors = sorted(c["percent_error"] for c in expected)
        median = errors[len(errors) // 2] if len(errors) % 2 else (
            errors[len(errors) // 2 - 1] + errors[len(errors) // 2]
        ) / 2
        assert headline["median"] == pytest.approx(median)
        assert headline["max"] == pytest.approx(errors[-1])
        assert headline["min"] == pytest.approx(errors[0])


def test_survey_records_the_licence_and_identity_of_every_series():
    """A data-availability statement needs a UID and a licence per series.

    The archive index does not label every series with a licence; those are counted
    rather than dropped, because they are the ones a citation has to resolve by hand.
    """
    payload = _load("survey.json")
    readable = [r for r in payload["series"] if not r["error"]]
    assert readable
    assert all(r["series_uid"] for r in readable)
    assert all("licence" in r for r in readable)

    missing = sum(1 for r in readable if not r["licence"])
    reported = payload["tag_absence"]["overall"]["n_series_without_licence"]
    assert reported == missing
    assert missing < len(readable), "no series carried a licence at all -- check the index query"
    assert "TCIA Data Usage Policy" in payload["data_use_note"]


def test_survey_records_the_parameters_needed_to_reproduce_it():
    parameters = _load("survey.json")["parameters"]
    for key in ("seed", "screen_per_vendor", "fallback_per_vendor", "modality", "api"):
        assert key in parameters
    provenance = _load("survey.json")["provenance"]
    assert provenance["archive"].startswith("The Cancer Imaging Archive")
    assert provenance["access_started_utc"]
