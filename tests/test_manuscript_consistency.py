"""Every number in the manuscript must be re-derivable from ``results/``.

This is the anti-fabrication gate. Each test recomputes a value from the shipped result
files and asserts that the exact formatted string appears in the manuscript, so a figure
that was edited by hand, or left stale after a re-run of the tools, fails CI rather than
reaching a reviewer.

It deliberately checks the *string*, not a parsed value: the failure mode being guarded
against is a number in the prose drifting away from the data, and only a literal match
catches that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MANUSCRIPT = REPO / "paper" / "ctdose-core_jacmp.md"
RESULTS = REPO / "results"
FIGURE_DATA = REPO / "paper" / "figures" / "figure_data.json"


def _load(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f"{path.relative_to(REPO)} not generated")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def text() -> str:
    if not MANUSCRIPT.exists():
        pytest.skip("manuscript not written")
    return MANUSCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def layer1() -> dict:
    return _load(RESULTS / "rdsr_layer1.json")


@pytest.fixture(scope="module")
def survey() -> dict:
    return _load(RESULTS / "survey.json")


def assert_quoted(text: str, value: str, label: str) -> None:
    assert value in text, f"{label}: {value!r} is not in the manuscript"


# --- Layer 1 -------------------------------------------------------------------------------


def test_layer1_counts_are_quoted_correctly(text, layer1):
    s = layer1["summary"]
    files = [f for f in layer1["files"] if f.get("is_ct_rdsr")]
    n_models = len({f["model"] for f in files})

    assert_quoted(text, f"{s['files_seen']} sample objects", "files seen")
    assert_quoted(text, f"{s['files_parsed_as_ct_rdsr']} parsed", "files parsed")
    assert_quoted(text, f"{len(s['vendors'])} manufacturers", "vendor count")
    assert_quoted(text, f"{n_models} scanner models", "model count")
    assert_quoted(text, f"{s['total_irradiation_events']} irradiation events", "event count")
    assert_quoted(text, f"{s['events_with_ctdivol']} carry a CTDIvol", "events with CTDIvol")


def test_layer1_dose_ranges_are_quoted_correctly(text, layer1):
    events = layer1["events"]
    ctdi = [e["ctdivol_mgy"] for e in events if e["ctdivol_mgy"] is not None]
    dlp = [e["dlp_mgycm"] for e in events if e["dlp_mgycm"] is not None]
    assert_quoted(text, f"{min(ctdi):g}–{max(ctdi):g} mGy", "CTDIvol range")
    assert_quoted(text, f"{min(dlp):g}–{max(dlp):g} mGy·cm", "DLP range")


def test_layer1_consistency_checks_are_quoted_correctly(text, layer1):
    check = layer1["summary"]["dlp_sum_check"]
    counts = layer1["summary"]["event_count_check"]
    assert check["files_within_tolerance"] == check["files_comparable"], (
        "the manuscript claims a clean pass; the data no longer supports it"
    )
    assert counts["files_matching"] == counts["files_declaring_a_count"]
    assert_quoted(
        text,
        f"within 1% in {check['files_within_tolerance']} of {check['files_comparable']}",
        "DLP sum check",
    )
    assert_quoted(
        text,
        f"declares in {counts['files_matching']} of {counts['files_declaring_a_count']}",
        "event count check",
    )


# --- Layer 2a ------------------------------------------------------------------------------


def test_screening_totals_are_quoted_correctly(text, survey):
    overall = survey["tag_absence"]["overall"]
    by_vendor = survey["tag_absence"]["by_vendor"]
    n_collections = len(overall["collections"])
    per_vendor = {b["n_series_readable"] for b in by_vendor.values()}
    assert len(per_vendor) == 1, "the manuscript states one sample size per manufacturer"

    assert_quoted(text, f"{overall['n_series_screened']} public CT series", "n screened")
    assert_quoted(text, f"{per_vendor.pop()} per manufacturer", "n per manufacturer")
    assert_quoted(text, f"{n_collections} collections", "n collections")
    assert_quoted(
        text,
        f"{overall['with_ctdivol_tag']} of {overall['n_series_readable']} series",
        "series with CTDIvol",
    )


@pytest.mark.parametrize("vendor", ["GE", "Siemens", "Philips", "Canon/Toshiba"])
def test_per_vendor_absence_rates_are_quoted_correctly(text, survey, vendor):
    block = survey["tag_absence"]["by_vendor"][vendor]
    rate = f"{100 * block['ctdivol_absence_rate']:.1f}%"
    assert_quoted(text, f"{rate} for {vendor}", f"{vendor} absence rate")


def test_overall_rates_are_quoted_correctly(text, survey):
    overall = survey["tag_absence"]["overall"]
    assert_quoted(text, f"{100 * overall['ctdivol_absence_rate']:.1f}%", "overall absence")
    assert_quoted(
        text, f"{100 * overall['fallback_feasibility_rate']:.1f}% of series", "feasibility"
    )
    assert_quoted(text, f"{100 * overall['surveyable_rate']:.1f}% of series", "surveyable")


def test_no_series_carried_a_dlp_record(text, survey):
    """The manuscript states this absolutely; the data must support it absolutely."""
    overall = survey["tag_absence"]["overall"]
    assert overall["with_dlp_record"] == 0
    assert "No series in the sample carried a usable DLP record" in text


def test_model_resolution_counts_are_quoted_correctly(text, survey):
    res = survey["tag_absence"]["overall"]["model_resolution"]
    assert_quoted(text, f"resolved {res['resolved']} model names", "models resolved")
    assert_quoted(text, f"found {res['ambiguous']} ambiguous", "models ambiguous")
    assert_quoted(text, f"did not cover {res['unknown']}", "models unknown")

    unmatched = list(survey["tag_absence"]["unmatched_models"].items())
    for name, count in unmatched[:4]:
        assert_quoted(text, f"`{name}` ({count} series)" if name == unmatched[0][0] else f"({count})",
                      f"unmatched model {name}")


def test_vendor_spread_claim_is_supported(text, survey):
    """'differing by up to N percentage points' must equal max - min."""
    rates = [100 * b["ctdivol_absence_rate"] for b in survey["tag_absence"]["by_vendor"].values()]
    spread = max(rates) - min(rates)
    assert_quoted(text, f"up to {spread:.0f} percentage points", "vendor spread")


def test_unsurveyable_fraction_claim_is_supported(text, survey):
    """'roughly three in ten' must be consistent with the measured complement."""
    unsurveyable = 100 * (1 - survey["tag_absence"]["overall"]["surveyable_rate"])
    assert 25.0 <= unsurveyable <= 35.0, (
        f"the manuscript says 'three in ten'; the data says {unsurveyable:.1f}%"
    )


# --- Layer 2b ------------------------------------------------------------------------------


def test_fallback_case_counts_and_exclusions_are_quoted_correctly(text, survey):
    fb = survey["fallback_error"]
    exclusions = survey["fallback_exclusions"]
    assert_quoted(text, f"{fb['overall']['n']} could be evaluated", "n evaluable")
    assert_quoted(
        text,
        f"{fb['independence']['n_distinct_recorded_estimated_pairs']} distinct",
        "distinct pairs",
    )
    assert_quoted(text, f"coefficient table ({exclusions['model_not_in_table']})", "excl model")
    assert_quoted(text, f"size limit ({exclusions['series_too_large']})", "excl size")
    assert_quoted(text, f"incomplete ({exclusions['insufficient_parameters']})", "excl params")


def test_headline_stratum_is_quoted_correctly(text, survey):
    """The number the paper's accuracy claim rests on."""
    h = survey["fallback_error"]["diagnostic_and_collimation_measured"]
    assert_quoted(text, f"n = {h['n']}", "headline n")
    assert_quoted(text, f"median relative error of +{h['median']:.1f}%", "headline median")
    assert_quoted(
        text,
        f"interquartile range −{abs(h['p25']):.1f}% to +{h['p75']:.1f}%",
        "headline IQR",
    )
    assert_quoted(text, f"mean absolute error {h['mean_absolute']:.1f}%", "headline MAE")
    assert_quoted(
        text, f"{100 * h['within_20_percent']:.1f}% of cases within ±20%", "within 20"
    )
    assert_quoted(text, f"{100 * h['within_50_percent']:.1f}% within ±50%", "within 50")
    assert_quoted(
        text, f"range −{abs(h['min']):.1f}% to +{h['max']:.1f}%", "headline range"
    )


def test_collimation_strata_medians_are_quoted_correctly(text, survey):
    strata = survey["fallback_error"]["by_collimation_provenance"]
    measured, reconstructed = strata["measured"], strata["reconstructed"]
    assert_quoted(
        text,
        f"+{measured['median']:.1f}% for the {measured['n']} cases with a recorded collimation",
        "measured stratum",
    )
    assert_quoted(
        text,
        f"+{reconstructed['median']:.1f}% for the {reconstructed['n']} without",
        "reconstructed stratum",
    )
    assert_quoted(text, f"median relative error was +{survey['fallback_error']['overall']['median']:.1f}%",
                  "pooled median")


def test_vendor_collimation_confound_is_quoted_correctly(text, survey):
    counts = survey["fallback_error"]["vendor_by_collimation_provenance"]
    siemens, canon, philips = counts["Siemens"], counts["Canon/Toshiba"], counts["Philips"]
    assert_quoted(
        text,
        f"Siemens contributed {siemens['collimation_measured']} with a recorded collimation "
        f"and none without",
        "Siemens split",
    )
    assert_quoted(
        text,
        f"Canon/Toshiba {canon['collimation_measured']} with and "
        f"{canon['collimation_reconstructed']} without",
        "Canon split",
    )
    assert_quoted(
        text,
        f"Philips none with and {philips['collimation_reconstructed']} without",
        "Philips split",
    )
    assert siemens["collimation_reconstructed"] == 0
    assert philips["collimation_measured"] == 0


def test_headline_per_vendor_is_quoted_correctly(text, survey):
    by_vendor = survey["fallback_error"]["diagnostic_and_collimation_measured"]["by_vendor"]
    for vendor in ("Canon/Toshiba", "Siemens"):
        d = by_vendor[vendor]
        assert d["n"] > 0
        assert_quoted(text, f"{vendor} (n = {d['n']})", f"{vendor} headline n")
        assert_quoted(text, f"+{d['median']:.1f}%", f"{vendor} headline median")
    assert by_vendor["Philips"]["n"] == 0, "the manuscript states Philips is unvalidated"
    assert "unvalidated for Philips" in text


def test_diagnostic_threshold_is_quoted_correctly(text, survey):
    threshold = survey["fallback_error"]["diagnostic_acquisitions_only"]["threshold_mgy"]
    remaining = survey["fallback_error"]["diagnostic_acquisitions_only"]["n"]
    assert_quoted(text, f"recorded CTDIvol ≥ {threshold} mGy", "threshold")
    assert_quoted(text, f"leaves {remaining} cases", "n diagnostic")


# --- figure-derived numbers -----------------------------------------------------------------


def test_demonstration_series_numbers_are_quoted_correctly(text):
    data = _load(FIGURE_DATA)
    fig4 = data.get("fig4_modulation_and_wed")
    if fig4 is None:
        pytest.skip("image figures not generated")
    assert_quoted(text, f"{fig4['n_slices']} slices", "n slices")
    assert_quoted(text, f"{fig4['kvp']:.0f} kV", "kVp")
    assert_quoted(
        text,
        f"{fig4['min_tube_current_ma']:.0f}–{fig4['max_tube_current_ma']:.0f} mA",
        "tube current range",
    )
    assert_quoted(text, f"mean of {fig4['mean_tube_current_ma']:.1f} mA", "mean mA")


def test_organ_index_numbers_are_quoted_correctly(text):
    data = _load(FIGURE_DATA)
    fig5 = data.get("fig5_organ_overlay")
    if fig5 is None:
        pytest.skip("organ figure not generated")
    assert_quoted(text, f"all {fig5['n_organs']} segmented structures", "n organs")
    assert_quoted(text, f"{fig5['w_min']:.3f} to {fig5['w_max']:.3f}", "w_o range")
    assert_quoted(
        text, f"{fig5['tube_current_range_pct_of_mean']:.1f}% of the mean", "modulation depth"
    )


# --- integrity of the manuscript itself -------------------------------------------------------


def test_the_flagged_pediatric_drl_is_not_used_as_a_result(text):
    """The unverified J-RIME literal must be declared, not quoted as a finding."""
    assert "We have not been able to verify the published J-RIME value" in text
    assert "excluded from any analysis in this work" in text
    assert "No paediatric DRL comparison is reported here" in text


def test_affiliation_and_licensing_are_correct(text):
    assert "Institute of One, LISIT Co., Ltd., Tokyo, Japan" in text
    assert "MIT" in text
    assert "Apache-2.0" in text
    assert "GPL-3.0" in text
    # The excluded institution must appear nowhere in the manuscript.
    for forbidden in ("National Cancer Center", "NCC", "国立がん研究センター"):
        assert forbidden not in text, f"{forbidden!r} must not appear"


def test_ai_and_conflict_declarations_are_present(text):
    for required in (
        "Conflicts of interest",
        "Funding",
        "Ethics approval",
        "Data and code availability",
        "Use of artificial intelligence",
        "The AI system is not an author",
        "TotalSegmentator (Apache-2.0) was used for inference only",
    ):
        assert required in text, f"missing declaration: {required}"


def test_every_figure_referenced_exists(text):
    referenced = set(re.findall(r"\*\*Figure (\d)\.\*\*", text))
    assert referenced == {"1", "2", "3", "4", "5", "6"}
    figdir = REPO / "paper" / "figures"
    for stem in (
        "fig1_layer1_rdsr_agreement",
        "fig2_recorded_dose_availability",
        "fig3_fallback_error",
        "fig4_modulation_and_wed",
        "fig5_organ_overlay",
        "fig6_organ_exposure_index",
    ):
        assert (figdir / f"{stem}.png").exists(), f"missing figure file: {stem}.png"


def test_submission_kit_agrees_with_the_manuscript(text):
    """The kit quotes the manuscript's headline numbers into the cover letter."""
    kit_path = REPO / "paper" / "jacmp_submission_kit.md"
    if not kit_path.exists():
        pytest.skip("submission kit not written")
    kit = kit_path.read_text(encoding="utf-8")

    words = len(text.split())
    n_refs = len(re.findall(r"^\d+\. ", text, flags=re.MULTILINE))
    n_figs = len(re.findall(r"\*\*Figure \d\.\*\*", text))
    assert f"| {words} |" in kit, f"the kit's word count is stale (manuscript has {words})"
    assert f"| {n_refs} |" in kit, f"the kit's reference count is stale ({n_refs})"
    assert f"{n_figs} figures" in kit, f"the kit's figure count is stale ({n_figs})"

    # Figures quoted in the cover letter must be the ones the manuscript reports.
    survey = json.loads((RESULTS / "survey.json").read_text(encoding="utf-8"))
    overall = survey["tag_absence"]["overall"]
    rates = [100 * b["ctdivol_absence_rate"] for b in survey["tag_absence"]["by_vendor"].values()]
    for value in (
        f"{100 * overall['ctdivol_absence_rate']:.1f}%",
        f"{min(rates):.1f}% to {max(rates):.1f}%",
        f"{overall['n_series_screened']} public CT series",
    ):
        assert value in kit, f"cover letter is stale: {value!r}"


def test_submission_kit_declares_what_is_unverified():
    """Anything the kit could not confirm from a primary source must say so."""
    kit_path = REPO / "paper" / "jacmp_submission_kit.md"
    if not kit_path.exists():
        pytest.skip("submission kit not written")
    kit = kit_path.read_text(encoding="utf-8")
    assert "[verify]" in kit
    assert "Institute of One, LISIT Co., Ltd., Tokyo, Japan" in kit
    assert "CC BY" in kit and "MIT" in kit
    for forbidden in ("National Cancer Center", "NCC", "国立がん研究センター"):
        assert forbidden not in kit, f"{forbidden!r} must not appear"


def test_within_the_jacmp_limits(text):
    """JACMP Research Article: 7500 words total, <=10 figures and tables, <=50 references."""
    words = len(text.split())
    assert words <= 7500, f"manuscript is {words} words; JACMP allows 7500"
    n_refs = len(re.findall(r"^\d+\. ", text, flags=re.MULTILINE))
    assert n_refs <= 50, f"{n_refs} references; JACMP allows 50"
    n_figures = len(re.findall(r"\*\*Figure \d\.\*\*", text))
    n_tables = len(re.findall(r"\*\*Table \d\.\*\*", text))
    assert n_figures + n_tables <= 10, f"{n_figures + n_tables} display items; JACMP allows 10"
