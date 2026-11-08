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


# --- the GE validation arm -----------------------------------------------------------


def test_ge_arm_accounts_for_every_event_it_saw():
    """Evaluated plus excluded must equal the events seen, with no event counted twice."""
    payload = _load("ge_rdsr_accuracy.json")
    summary = payload["summary"]

    assert summary["n_files"] == len(payload["files"])
    assert summary["n_events_seen"] == sum(f["n_events"] for f in payload["files"])
    assert summary["n_events_with_ctdivol"] == sum(
        f["n_events_with_ctdivol"] for f in payload["files"]
    )
    assert summary["n_evaluated"] == len(payload["cases"])
    assert summary["n_excluded"] == len(payload["excluded"])
    assert summary["n_evaluated"] + summary["n_excluded"] == summary["n_events_seen"]
    assert sum(summary["exclusions_by_reason"].values()) == summary["n_excluded"]

    seen = {(e["file"], e["event_index"]) for e in payload["excluded"]}
    seen |= {(c["file"], c["event_index"]) for c in payload["cases"]}
    assert len(seen) == summary["n_events_seen"], "an event was counted twice"


def test_ge_arm_percent_error_is_reproducible_from_the_two_values():
    """The recorded value is the denominator, recomputed here from the row itself."""
    for case in _load("ge_rdsr_accuracy.json")["cases"]:
        recorded = case["recorded_ctdivol_mgy"]
        estimated = case["estimated_ctdivol_mgy"]
        assert case["percent_error"] == pytest.approx(
            100.0 * (estimated - recorded) / recorded
        ), f"{case['file']} #{case['event_index']}"


def test_ge_arm_estimate_is_reproducible_from_the_formula():
    """CTDIvol = CTDIw * (mAs_rot / 100) / pitch, and mAs_rot = mean mA * t_rot."""
    for case in _load("ge_rdsr_accuracy.json")["cases"]:
        assert case["mas_per_rotation"] == pytest.approx(
            case["mean_tube_current_ma"] * case["exposure_time_per_rotation_s"]
        )
        assert case["estimated_ctdivol_mgy"] == pytest.approx(
            case["table_ctdiw_mgy_per_100mas"]
            * (case["mas_per_rotation"] / 100.0)
            / case["pitch"]
        ), f"{case['file']} #{case['event_index']}"
        # And the implied coefficient inverts it, which is what makes the narrow band
        # of implied values evidence about the formula rather than about the table.
        assert case["implied_nctdi_w_mgy_per_100mas"] == pytest.approx(
            case["recorded_ctdivol_mgy"] * case["pitch"] / (case["mas_per_rotation"] / 100.0)
        )


def test_ge_arm_only_compared_helical_ge_events():
    """Every row satisfies the eligibility rules the provenance block states."""
    payload = _load("ge_rdsr_accuracy.json")
    for case in payload["cases"]:
        assert case["acquisition_type"].lower() == "spiral acquisition"
        assert case["table_vendor"] == "GE"
        assert case["recorded_ctdivol_mgy"] > 0
        assert case["phantom_cm"] in (16, 32)
        assert case["pitch"] > 0
        assert case["kvp"] > 0


def test_ge_arm_separates_the_dose_model_from_the_recovery_path():
    """The provenance must say which claim this arm supports, and name its dependence.

    The technique here is read from a structured report, not reconstructed from a
    de-identified image header, so this validates the dose model and not the
    metadata-gap recovery the paper is about. The manuscript must not merge the two, and
    the events are not independent patients.
    """
    provenance = _load("ge_rdsr_accuracy.json")["provenance"]
    assert provenance["generated_by"].endswith("evaluate_ge_rdsr.py")
    assert "does NOT test the metadata-gap recovery path" in provenance["what_this_tests"]
    assert "not independent patients" in provenance["independence"]
    assert "OpenREM" in provenance["sample_source"]
    assert "GPL" in provenance["sample_source"]
    assert "not redistributed here" in provenance["sample_source"]
    assert len(provenance["eligibility_rules"]) >= 4


def test_ge_arm_readme_counts_come_from_the_results_file():
    """The README's prose about this arm must be resolved from the numbers, not recalled.

    "Of 35 GE irradiation events, 15 record a CTDIvol and 4 enter the comparison" is the
    kind of sentence that stays in a README for two revisions after the run changed. Each
    figure it states is checked against the file here, so the prose cannot drift.
    """
    payload = _load("ge_rdsr_accuracy.json")
    summary = payload["summary"]
    readme = (RESULTS / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Reading the GE arm", 1)
    assert len(section) == 2, "the README no longer documents the GE arm"
    text = section[1]

    assert f"{summary['n_events_seen']} GE irradiation events" in text
    assert f"{summary['n_events_with_ctdivol']} record a CTDIvol" in text
    assert f"{summary['n_evaluated']} enter the comparison" in text

    refused = sum(
        1 for e in payload["excluded"]
        if e["reason"] == "scanner not in the open coefficient database"
    )
    assert "Two more are refused" in text and refused == 2

    # The scan-duration figures the README quotes, rounded as it quotes them.
    worst = max(
        payload["cases"],
        key=lambda c: c["beam_width_consistency"]["at_collimation_mm"][
            f"{c['recorded_collimation_mm']:g}"
        ]["scan_duration_s"],
    )
    at = worst["beam_width_consistency"]["at_collimation_mm"]
    recorded_key = f"{worst['recorded_collimation_mm']:g}"
    assert (
        f"{worst['beam_width_consistency']['scanning_length_mm']:.0f} mm helical pass"
        in text
    )
    assert f"{at[recorded_key]['scan_duration_s']:.0f} s of continuous rotation" in text
    assert f"at 40 mm it needs {at['40']['scan_duration_s']:.0f} s" in text


def test_ge_arm_proved_the_formula_ignores_the_fields_it_invented():
    """An RDSR records no slice thickness or scan range; the check must have run."""
    check = _load("ge_rdsr_accuracy.json")["formula_input_check"]
    assert check["invariant"] is True
    assert (
        check["estimate_with_5mm_slice_100mm_range"]
        == check["estimate_with_0.625mm_slice_700mm_range"]
    )


def test_ge_arm_collimation_sensitivity_is_marked_as_sensitivity():
    """The headline must be the recorded collimation, not the best-agreeing row.

    The recorded 2.5-5 mm beams snap to the narrowest row tabulated for these two
    models, 10 mm (it is 5 mm for Revolution CT ES), and the
    20 mm row would agree far better. Picking it would fit one free parameter to four
    observations, so the alternatives are reported and labelled.
    """
    summary = _load("ge_rdsr_accuracy.json")["summary"]
    sensitivity = summary["collimation_sensitivity"]
    assert "sensitivity only" in sensitivity["note"]
    assert "fit a free parameter" in sensitivity["note"]
    assert summary["overall"]["n"] == sum(
        1 for _ in _load("ge_rdsr_accuracy.json")["cases"]
    )
    # Every case used the collimation the report recorded, not a chosen one. Where the
    # table had no such row, the substitution must be written on the case, naming both
    # the recorded width and the row actually used.
    for case in _load("ge_rdsr_accuracy.json")["cases"]:
        recorded = case["recorded_collimation_mm"]
        used = case["table_collimation_mm"]
        if recorded == used:
            continue
        expected = (
            f"collimation {recorded:.1f} mm is not tabulated; used the nearest "
            f"measured value, {used:.1f} mm"
        )
        assert any(expected in a for a in case["assumptions"]), (
            f"{case['file']} #{case['event_index']}: the substitution from the recorded "
            f"collimation must be recorded as an assumption naming both widths; got "
            f"{case['assumptions']}"
        )
