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

from ctdose_core.tables import drl_tables

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


def _signed(value: float, digits: int = 1) -> str:
    """A percentage with its sign, the way the manuscript writes it.

    The sign has to come from the value rather than from the sentence: an earlier version
    of this test hard-coded a leading minus on the lower quartile, which silently stopped
    checking anything once the quartile turned positive.
    """
    return f"{'−' if value < 0 else '+'}{abs(value):.{digits}f}%"


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


def test_absent_count_is_paired_with_the_absence_rate(text, survey):
    """The defect this suite failed to catch on the first pass.

    Asserting only that a count appears somewhere is not enough: the count has to be the
    one the sentence names. Here the absent count and the absence rate must appear
    together, and the *present* count must never be described as absent.
    """
    overall = survey["tag_absence"]["overall"]
    absent = overall["without_ctdivol_tag"]
    present = overall["with_ctdivol_tag"]
    total = overall["n_series_readable"]
    rate = f"{100 * overall['ctdivol_absence_rate']:.1f}%"

    assert absent != present, "this test cannot discriminate when the split is even"
    assert_quoted(text, f"{absent} of {total} series ({rate})", "absent count with its rate")

    # The present count must not appear within a sentence that says "absent".
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if "absent" in sentence.lower() and str(present) in sentence:
            raise AssertionError(
                f"the count {present} (series that HAVE CTDIvol) appears in a sentence "
                f"about absence: {sentence[:160]!r}"
            )


def test_screening_totals_are_quoted_correctly(text, survey):
    overall = survey["tag_absence"]["overall"]
    by_vendor = survey["tag_absence"]["by_vendor"]
    n_collections = len(overall["collections"])
    per_vendor = {b["n_series_readable"] for b in by_vendor.values()}
    assert len(per_vendor) == 1, "the manuscript states one sample size per manufacturer"

    assert_quoted(text, f"{overall['n_series_screened']} public CT series", "n screened")
    assert_quoted(text, f"{per_vendor.pop()} per manufacturer", "n per manufacturer")
    assert_quoted(text, f"{n_collections} collections", "n collections")
    # The count of series that HAVE a recorded CTDIvol is used to open the accuracy arm.
    assert_quoted(
        text,
        f"Of the {overall['with_ctdivol_tag']} screened series carrying a recorded CTDIvol",
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
        text,
        f"complete enough in {100 * overall['fallback_feasibility_rate']:.1f}%",
        "marginal feasibility",
    )
    assert_quoted(
        text,
        f"{overall['surveyable']} of {overall['n_series_readable']} series "
        f"({100 * overall['surveyable_rate']:.1f}%)",
        "surveyable",
    )


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


def test_conditional_reconstructability_is_quoted_correctly(text, survey):
    """The conditional rate is the one the reconstruction's usefulness rests on."""
    overall = survey["tag_absence"]["overall"]
    assert_quoted(
        text,
        f"{overall['without_ctdivol_tag']} series that lacked a recorded CTDIvol, "
        f"{overall['n_without_ctdivol_reconstructable']} "
        f"({100 * overall['conditional_reconstructability_rate']:.1f}%)",
        "overall conditional rate",
    )
    for vendor, block in survey["tag_absence"]["by_vendor"].items():
        rate = 100 * (block["conditional_reconstructability_rate"] or 0.0)
        assert_quoted(text, f"| **{rate:.1f}%** |", f"{vendor} conditional rate in Table 1")
    assert_quoted(
        text,
        f"{overall['surveyable']} of {overall['n_series_readable']} series "
        f"({100 * overall['surveyable_rate']:.1f}%)",
        "surveyable total",
    )
    assert_quoted(
        text,
        f"{overall['n_series_readable'] - overall['surveyable']} "
        f"({100 * (1 - overall['surveyable_rate']):.1f}%)",
        "unsurveyable total",
    )


def test_fallback_case_counts_and_exclusions_reconcile(text, survey):
    """The exclusion arithmetic in the text must actually add up.

    The categories overlap, so they cannot be subtracted sequentially; the manuscript has
    to state the once-only excluded total and the sampling cap that follows it.
    """
    fb = survey["fallback_error"]
    exclusions = survey["fallback_exclusions"]
    rows = survey["series"]
    has = [r for r in rows if r["has_ctdivol_tag"] and not r["error"]]
    eligible = [
        r for r in has
        if r["model_resolution"] != "unknown" and (r["n_images"] or 0) <= 250
    ]
    both = [
        r for r in has
        if r["model_resolution"] == "unknown" and (r["n_images"] or 0) > 250
    ]
    excluded_once = len(has) - len(eligible)
    cap = survey["parameters"]["fallback_per_vendor"]

    assert excluded_once == (
        exclusions["model_not_in_table"] + exclusions["series_too_large"] - len(both)
    ), "the overlap accounting is wrong"

    assert_quoted(text, f"Of the {len(has)} screened series", "n with recorded CTDIvol")
    assert_quoted(text, f"{excluded_once} were excluded", "excluded once")
    assert_quoted(text, f"table ({exclusions['model_not_in_table']})", "excl model")
    assert_quoted(text, f"limit ({exclusions['series_too_large']})", "excl size")
    assert_quoted(text, f"{len(both)} series fell into both categories", "overlap")
    assert_quoted(text, f"{len(eligible)} remaining eligible series", "eligible")
    assert_quoted(text, f"capped at {cap} per manufacturer", "sampling cap")
    assert_quoted(
        text, f"{exclusions['insufficient_parameters']} lacked complete", "excl params"
    )
    assert_quoted(text, f"leaving {fb['overall']['n']} evaluable cases", "n evaluable")
    assert_quoted(
        text,
        f"{fb['independence']['n_distinct_recorded_estimated_pairs']} distinct",
        "distinct pairs",
    )


def test_headline_stratum_is_quoted_correctly(text, survey):
    """The number the paper's accuracy claim rests on."""
    h = survey["fallback_error"]["diagnostic_and_collimation_measured"]
    assert_quoted(text, f"n = {h['n']}", "headline n")
    assert_quoted(text, f"median relative error of +{h['median']:.1f}%", "headline median")
    assert_quoted(
        text,
        f"interquartile range {_signed(h['p25'])} to {_signed(h['p75'])}",
        "headline IQR",
    )
    assert_quoted(text, f"mean absolute error {h['mean_absolute']:.1f}%", "headline MAE")
    assert_quoted(
        text, f"{100 * h['within_20_percent']:.1f}% of cases within ±20%", "within 20"
    )
    assert_quoted(text, f"{100 * h['within_50_percent']:.1f}% within ±50%", "within 50")
    assert_quoted(
        text, f"range {_signed(h['min'])} to {_signed(h['max'])}", "headline range"
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
    # Spelled out rather than "≥": U+2265 is outside WinAnsi and renders as a black
    # square in the submission PDF. See tools/build_manuscript_pdf.py::assert_encodable.
    assert_quoted(text, f"recorded CTDIvol of at least {threshold} mGy", "threshold")
    assert_quoted(text, f"leaves {remaining} cases", "n diagnostic")


# --- size correction and effective dose -------------------------------------------------------


@pytest.fixture(scope="module")
def size_and_dose() -> dict:
    return _load(RESULTS / "size_and_dose.json")


def test_size_layer_inclusion_accounting_is_quoted_correctly(text, size_and_dose):
    s = size_and_dose["summary"]
    assert s["n_contour_failed"] == 1, "the manuscript spells this count as 'one'"
    assert_quoted(text, f"Of the {s['n_cases_in_survey']}, one could not be contoured", "contour failure")
    assert_quoted(text, f"leaving {s['n_evaluated']}", "n evaluated")
    assert_quoted(text, f"{s['n_phantom_collection']} scans of physical test objects", "phantoms")
    assert_quoted(text, f"{s['n_too_few_slices']} series of fewer than 10 slices", "short series")
    assert_quoted(text, f"**{s['n_patient_like']} patient-like series**", "patient-like n")


def test_size_layer_statistics_are_quoted_correctly(text, size_and_dose):
    p = size_and_dose["summary"]["patient_like"]
    wed, factor, ssde_ = (
        p["water_equivalent_diameter_cm"], p["ssde_conversion_factor"], p["ssde_from_recorded_mgy"]
    )
    assert_quoted(text, f"median of {wed['median']:.1f} cm", "WED median")
    assert_quoted(text, f"{wed['p25']:.1f}–{wed['p75']:.1f} cm", "WED IQR")
    assert_quoted(text, f"range {wed['min']:.1f}–{wed['max']:.1f} cm", "WED range")
    assert_quoted(text, f"median of {factor['median']:.2f}", "factor median")
    assert_quoted(text, f"range {factor['min']:.2f}–{factor['max']:.2f}", "factor range")
    assert_quoted(text, f"median of {ssde_['median']:.1f} mGy", "SSDE median")


def test_effective_dose_is_quoted_with_its_reduced_denominator(text, size_and_dose):
    """Three series have no recoverable scan range; the paper must say so, not average them."""
    s = size_and_dose["summary"]
    e = s["patient_like"]["effective_dose_msv"]
    assert e["n"] == s["n_patient_like_with_scan_range"]
    assert_quoted(text, f"**{e['n']} of the {s['n_patient_like']}**", "effective-dose denominator")
    assert_quoted(text, f"median of {e['median']:.1f} mSv", "E median")
    assert_quoted(text, f"{e['p25']:.1f}–{e['p75']:.1f} mSv", "E IQR")
    assert_quoted(text, f"range {e['min']:.1f}–{e['max']:.1f} mSv", "E range")
    assert "identical longitudinal position" in text


# --- figure-derived numbers -----------------------------------------------------------------


def test_demonstration_series_numbers_are_quoted_correctly(text):
    data = _load(FIGURE_DATA)
    fig4 = data.get("fig4_modulation_and_wed")
    if fig4 is None:
        pytest.skip("image figures not generated")
    assert_quoted(text, f"{fig4['n_slices']} images", "image count")
    assert_quoted(text, f"{fig4['kvp']:.0f} kVp", "kVp")
    assert_quoted(
        text,
        f"{fig4['min_tube_current_ma']:.0f}–{fig4['max_tube_current_ma']:.0f} mA",
        "tube current range",
    )
    assert_quoted(text, f"mean of {fig4['mean_tube_current_ma']:.1f} mA", "mean mA")


def test_the_demonstration_case_is_public_and_identified(text):
    """Figures made from images a reader cannot obtain are not reproducible.

    The figure script records what it actually read; this binds the manuscript to it, so
    the text cannot describe one dataset while the figures show another -- which is
    exactly what happened when an earlier draft's private series was replaced.
    """
    data = _load(FIGURE_DATA)
    fig4 = data.get("fig4_modulation_and_wed")
    if fig4 is None:
        pytest.skip("image figures not generated")
    assert fig4["public_case"], "the image figures were generated from a non-public series"
    for field in ("collection", "subject_id"):
        assert_quoted(text, str(fig4[field]), f"demonstration {field}")
    assert "not redistributed" in text or "are not redistributed" in text

    # The Series Instance UID is deliberately *not* in the manuscript body -- it is 40
    # unreadable digits mid-sentence -- but a figure a reader cannot re-fetch is not
    # reproducible, so it has to be exactly where the manuscript says it is.
    guide = REPO / "docs" / "REPRODUCING_FIGURES.md"
    assert guide.exists(), "the manuscript points readers at a retrieval guide that is missing"
    assert_quoted(text, "docs/REPRODUCING_FIGURES.md", "pointer to the retrieval guide")
    assert_quoted(
        guide.read_text(encoding="utf-8"),
        str(fig4["series_instance_uid"]),
        "demonstration UID in the retrieval guide",
    )


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


def test_the_external_validation_is_quoted_correctly(text):
    """Section 3.6 quotes the reference doses read from the collection's own report."""
    path = RESULTS / "tcia_validation.json"
    if not path.exists():
        pytest.skip("validation not run")
    payload = _load(path)
    cases = {c["subject_id"]: c for c in payload["cases"] if "skipped" not in c}
    assert cases, "no validation case was evaluated"
    for subject, c in cases.items():
        assert_quoted(text, subject, f"{subject} subject id")
        assert_quoted(
            text,
            f"published {c['reference_ctdivol_mgy']:.2f} mGy",
            f"{subject} reference CTDIvol",
        )
        assert_quoted(
            text,
            f"{c['reconstructed_ctdivol_corrected_mgy']:.2f} mGy",
            f"{subject} reconstructed CTDIvol",
        )
    # The reference must not be transcribed into the repository: it is read at run time
    # from the published report, and that is what makes it an external reference.
    assert payload["provenance"]["reference_read_from"]["file"].endswith(".xlsx")


def test_the_uncertainty_on_the_headline_claims_is_quoted_correctly(text):
    """A point estimate quoted bare is not a result; the interval must be in the text."""
    path = RESULTS / "inferential_stats.json"
    if not path.exists():
        pytest.skip("inferential statistics not computed")
    stats = _load(path)
    h = stats["headline_error_median"]
    assert_quoted(
        text,
        f"95% percentile-bootstrap CI {_signed(h['ci95_low'])} to {_signed(h['ci95_high'])}",
        "headline CI",
    )
    v = stats["vendor_effect_on_ctdivol_availability"]
    # Written as inline math so mathtext rasterises the chi with an embedded font; a
    # literal Greek chi is outside WinAnsi and renders as a black square.
    assert_quoted(text, rf"$\chi^2$({v['dof']}) = {v['chi2']:.1f}", "vendor chi-squared")
    assert_quoted(text, f"Cramér's V = {v['cramers_v']:.2f}", "Cramer's V")

    # A nominal p-value, not an exact tail probability: the sample is clustered by
    # collection, so the test's independence assumption does not hold and the effect
    # size carries the claim.
    assert "nominal p < 0.001" in text
    assert "may not be independent" in text
    assert "effect size is therefore emphasised" in text
    # Neither the p-value nor the bootstrap interval may be called a bound: clustering
    # does not sign the error in a way that justifies "upper" or "lower".
    assert "lower bound" not in text, "clustering does not make either statistic a bound"
    assert "p-value is a bound" not in text
    assert "may underestimate uncertainty" in text

    # The caveat must describe the stratum the interval is actually computed on. The
    # duplicate re-releases are a property of the 38 pooled cases; asserting they widen
    # the headline interval was wrong, because none of them is in the headline stratum.
    cav = stats["independence_caveat"]
    assert cav["headline_n_duplicate_pairs"] == 0
    assert_quoted(text, f"All {cav['headline_n']} are distinct", "headline distinctness")
    assert_quoted(
        text, f"from only {cav['headline_n_collections']} collections", "collection count"
    )


def test_the_tube_voltage_correction_is_reported_both_ways(text, survey):
    """A correction reported only in its corrected form is an assertion, not a result."""
    block = survey["fallback_error"]["by_tube_voltage"]
    off = block["off_reference_voltage"]
    assert_quoted(text, f"median error was {_signed(off['uncorrected']['median'])} uncorrected", "uncorrected")
    assert_quoted(text, f"{_signed(off['corrected']['median'])} corrected", "corrected")
    for kv, b in off["by_voltage"].items():
        assert_quoted(text, f"at {kv} kV (n = {b['n']})", f"{kv} kV stratum")


# --- integrity of the manuscript itself -------------------------------------------------------


def test_the_corrected_pediatric_drl_carries_its_primary_source(text):
    """The 141 mGy literal was a typo, and the correction must be checkable.

    An earlier draft declared the value unverifiable and made no claim about it. It has
    since been traced to the nationwide survey underlying the J-RIME 2015 paediatric
    DRLs, so the manuscript now asserts the corrected value -- which means the primary
    source and the numbers read from it have to be in the text, not just in the data.
    """
    drl = drl_tables()
    row = next(r for r in drl["pediatric"] if "source_value_ctdivol_mgy" in r)
    assert row["verified"] is True, "the manuscript asserts a value the table still flags"

    assert "141 mGy" in text
    assert f"{row['ctdivol_mgy']:.0f} mGy" in text
    # The primary-source values, quoted rather than paraphrased.
    assert "14.3 mGy" in text and "296.0 mGy·cm" in text
    assert "[19]" in text, "the correction must cite the source it was checked against"
    assert "No paediatric DRL comparison is reported in this work" in text
    # ...and the superseded hedge must be gone, not merely contradicted elsewhere.
    assert "We have not been able to verify the published J-RIME value" not in text


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


def test_figure_generation_is_deterministic():
    """§2.8 claims the figures are generated from the data; that requires reproducibility.

    The jitter in Figure 1 was originally seeded from ``hash()``, which Python randomises
    per process, so the figure differed on every run. This checks the replacement is
    stable, without re-rendering: identical inputs must give identical offsets.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_figures", REPO / "paper" / "figures" / "make_figures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    keys = ["CT-ESR-GE_VCT.dcm#0", "CT-RDSR-Siemens-Multi-3.dcm#2", "all cases#7"]
    first = [module.stable_jitter(k) for k in keys]
    second = [module.stable_jitter(k) for k in keys]
    assert first == second
    assert len(set(first)) == len(keys), "distinct keys must give distinct offsets"
    assert all(abs(v) <= 0.22 for v in first)
    # A different spread scales the same key proportionally rather than re-randomising.
    assert module.stable_jitter(keys[0], 0.11) == pytest.approx(first[0] / 2)


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

    # Read the compliance table's *actual* column rather than searching the whole file:
    # "| 7500 |" also matches the limit column, so a manuscript sitting exactly on the
    # limit made this assertion pass without the kit having been updated at all.
    def kit_row(label: str) -> tuple[str, str]:
        m = re.search(rf"^\|\s*{re.escape(label)}[^|]*\|([^|]*)\|([^|]*)\|", kit, re.MULTILINE)
        assert m, f"the kit has no compliance row for {label!r}"
        return m.group(1).strip(), m.group(2).strip()

    limit, actual = kit_row("Total word count")
    assert actual == str(words), (
        f"the kit's word count is stale: it says {actual}, the manuscript has {words}"
    )
    assert int(words) <= int(limit), f"{words} words exceeds the kit's own stated limit"

    _, actual_refs = kit_row("References")
    assert actual_refs == str(n_refs), (
        f"the kit's reference count is stale: it says {actual_refs}, there are {n_refs}"
    )
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
