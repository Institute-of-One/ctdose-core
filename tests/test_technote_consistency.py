"""The Technical Note and its supplement must hold to the full manuscript's standard.

The JACMP revision reformats the Research Article into a Technical Note. Three things can
go wrong in that reformat, and none of them would be caught by a test on the full
manuscript:

* a number is retyped while being shortened, and drifts from the data it came from;
* a result is dropped from the main text without arriving in the supplement, so it is
  silently lost rather than relocated;
* the deep-learning organ layer, which was withdrawn from this submission entirely so it
  can carry a separate Original Article, leaks back into either document and destroys the
  novelty it was withdrawn to protect.

This suite guards all three against the shipped result files, and holds the review-facing
builds to the article-type and masking rules the revision has to satisfy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "paper"
TECHNOTE = PAPER / "ctdose-core_jacmp_technote.md"
SUPPLEMENT = PAPER / "ctdose-core_jacmp_technote_supplement.md"
FULL = PAPER / "ctdose-core_jacmp.md"
RESULTS = REPO / "results"

#: JACMP counts every element of a Technical Note -- abstract, body, table, figure legends
#: and references alike -- so the limit is checked against the whole file rather than
#: against a body slice. The full manuscript is checked against its own 7500 elsewhere.
WORD_LIMIT = 3000

#: Terms that would betray the withdrawn organ layer. Checked against both documents,
#: because the whole point of withdrawing it is that none of it appears in JACMP.
ORGAN_TERMS = (
    "TotalSegmentator",
    "organ mask",
    "organ overlay",
    "Khatonabadi",
    "Tian",
    "organ-specific weighted",
    "regional CTDIvol",
    "absorbed organ dose",
    "organ dose estimation is",
    "L273",
    "LDCT-and-Projection",
    "tube-current modulation",
    "modulation depth",
    "segmented structures",
)


def _load(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f"{path.relative_to(REPO)} not generated")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def text() -> str:
    if not TECHNOTE.exists():
        pytest.skip("Technical Note not written")
    return TECHNOTE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def supplement() -> str:
    if not SUPPLEMENT.exists():
        pytest.skip("Supplementary Material not written")
    return SUPPLEMENT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def survey() -> dict:
    return _load(RESULTS / "survey.json")


@pytest.fixture(scope="module")
def layer1() -> dict:
    return _load(RESULTS / "rdsr_layer1.json")


@pytest.fixture(scope="module")
def size_and_dose() -> dict:
    return _load(RESULTS / "size_and_dose.json")


def assert_quoted(text: str, value: str, label: str) -> None:
    assert value in text, f"{label}: {value!r} is not in the document"


def _signed(value: float, digits: int = 1) -> str:
    return f"{'−' if value < 0 else '+'}{abs(value):.{digits}f}%"


# --- the constraints the reformat exists to satisfy ------------------------------------


def test_the_article_type_is_a_technical_note(text):
    """The Section Editor changed the type; the manuscript has to say so."""
    assert "**Article type:** Technical Note" in text
    assert "Research Article" not in text
    title_page = (PAPER / "title_page_jacmp.md").read_text(encoding="utf-8")
    assert "**Article type:** Technical Note" in title_page, (
        "the title page is uploaded separately and still declares the old article type"
    )
    assert "Research Article" not in title_page


def test_the_whole_manuscript_is_within_the_technical_note_limit(text):
    """Every element counts: abstract, body, table, legends and references."""
    words = len(text.split())
    assert words <= WORD_LIMIT, (
        f"the Technical Note is {words} words; the limit counting all elements is "
        f"{WORD_LIMIT}"
    )


def test_the_display_items_were_cut_to_three_figures_and_one_table(text):
    figures = re.findall(r"\*\*Figure (\d)\.\*\*", text)
    assert figures == ["1", "2", "3"], f"main text figures are {figures}; expected 1-3"
    for stem in ("fig1_layer1_rdsr_agreement", "fig2_recorded_dose_availability",
                 "fig3_fallback_error"):
        assert (PAPER / "figures" / f"{stem}.png").exists(), f"missing figure: {stem}.png"
    assert len(re.findall(r"\*\*Table \d\.\*\*", text)) == 1


def test_the_references_are_numbered_contiguously_and_all_cited(text):
    refs = re.findall(r"^(\d+)\. ", text, flags=re.MULTILINE)
    assert refs == [str(i) for i in range(1, len(refs) + 1)], (
        "the reference list is not numbered 1..n; trimming it left a gap"
    )
    body = text[:text.index("## References")]
    cited = {int(n) for group in re.findall(r"\[([\d,\s]+)\]", body)
             for n in group.split(",") if n.strip().isdigit()}
    assert cited == set(range(1, len(refs) + 1)), (
        f"citations {sorted(cited)} do not match the {len(refs)} listed references"
    )


def test_the_structured_abstract_headings_survived_the_condensation(text):
    abstract = text[text.index("## Abstract"):text.index("\n---\n", text.index("## Abstract"))]
    headings = re.findall(r"\*\*(\w+):\*\*", abstract)
    assert headings == ["Background", "Purpose", "Methods", "Results", "Conclusion"]


# --- the organ layer was withdrawn, not relocated ---------------------------------------


@pytest.mark.parametrize("term", ORGAN_TERMS)
def test_no_organ_layer_result_reaches_jacmp(text, supplement, term):
    """The organ work is reserved for a separate Original Article.

    Publishing any of it here -- in the main text or in the supplement, which is just as
    public once the paper appears -- would spend the novelty the withdrawal was meant to
    preserve. The single permitted mention is a forward-looking sentence naming no result,
    which contains none of these terms.
    """
    assert term not in text, f"{term!r} is in the Technical Note; the organ layer is withdrawn"
    assert term not in supplement, f"{term!r} is in the supplement; the organ layer is withdrawn"


def test_the_organ_figures_are_referenced_by_neither_document(text, supplement):
    for stem in ("fig4_modulation_and_wed", "fig5_organ_overlay", "fig6_organ_exposure_index"):
        assert stem not in text, f"{stem} is withdrawn but still in the main text"
        assert stem not in supplement, f"{stem} is withdrawn but still in the supplement"
    assert not re.findall(r"\*\*Figure S\d\.\*\*", supplement), (
        "the supplement carries no figures in this revision"
    )


def test_the_forward_looking_sentence_promises_nothing_it_does_not_report(text):
    """The one permitted mention of the organ work must state no finding."""
    assert "are the subject of separate work" in text
    sentence = next(s for s in re.split(r"(?<=[.!?])\s+", text) if "separate work" in s)
    assert not re.search(r"\d", sentence), (
        f"the forward-looking sentence quotes a number, which publishes a result: {sentence!r}"
    )


# --- what was moved to the supplement did arrive there ----------------------------------


def test_the_main_text_points_at_the_supplement(text):
    assert text.count("Supplementary Material") >= 1


@pytest.mark.parametrize(
    "phrase",
    [
        "diagnostic reference levels",   # the DRL comparison
        "patient-like series",           # the size and effective-dose cohort
        "141 mGy",                       # the data-quality finding
        "superseded run",                # the coefficient-swap comparison
        "template-blind sweep",          # the independent parser oracle
        "Aquilion",                      # the uncovered scanner names
    ],
)
def test_every_relocated_section_arrived_in_the_supplement(supplement, phrase):
    """Removed from the main text is only acceptable if present in the supplement."""
    assert phrase in supplement, f"{phrase!r} was cut from the main text and lost"


def test_the_supplement_carries_its_own_references_and_cites_them_all(supplement):
    refs = re.findall(r"^S(\d+)\. ", supplement, flags=re.MULTILINE)
    assert refs, "the supplement cites sources and must list them"
    assert refs == [str(i) for i in range(1, len(refs) + 1)]
    body = supplement[:supplement.index("## S3. Supplementary references")]
    cited = {int(n.strip().lstrip("S")) for group in re.findall(r"\[(S[\dS,\s]+)\]", body)
             for n in group.split(",") if n.strip().lstrip("S").isdigit()}
    assert cited == set(range(1, len(refs) + 1)), (
        f"supplementary citations {sorted(cited)} do not match the {len(refs)} listed"
    )


# --- the numbers, re-derived from the shipped results ------------------------------------


def test_layer1_numbers_are_quoted_correctly(text, layer1):
    s = layer1["summary"]
    files = [f for f in layer1["files"] if f.get("is_ct_rdsr")]
    ctdi = [e["ctdivol_mgy"] for e in layer1["events"] if e["ctdivol_mgy"] is not None]

    assert_quoted(text, f"{s['files_seen']} sample objects", "files seen")
    assert_quoted(text, f"{s['files_parsed_as_ct_rdsr']} parsed", "files parsed")
    assert_quoted(text, f"{len(s['vendors'])} manufacturers", "vendor count")
    assert_quoted(text, f"{len({f['model'] for f in files})} scanner models", "model count")
    assert_quoted(text, f"{s['total_irradiation_events']} irradiation events", "events")
    assert_quoted(text, f"{s['events_with_ctdivol']} carry a CTDIvol", "events with CTDIvol")
    assert_quoted(text, f"{min(ctdi):g}–{max(ctdi):g} mGy", "CTDIvol range")

    check = s["dlp_sum_check"]
    counts = s["event_count_check"]
    assert check["files_within_tolerance"] == check["files_comparable"]
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


def test_availability_numbers_are_quoted_correctly(text, survey):
    overall = survey["tag_absence"]["overall"]
    total = overall["n_series_readable"]
    rate = f"{100 * overall['ctdivol_absence_rate']:.1f}%"

    assert_quoted(text, f"{overall['n_series_screened']} public CT series", "n screened")
    assert_quoted(text, f"{len(overall['collections'])} collections", "n collections")
    assert_quoted(text, f"{overall['without_ctdivol_tag']} of {total} series ({rate})",
                  "absent count with its rate")
    for vendor, block in survey["tag_absence"]["by_vendor"].items():
        assert_quoted(text, f"{100 * block['ctdivol_absence_rate']:.1f}% for {vendor}",
                      f"{vendor} absence rate")
    assert_quoted(text, f"complete enough in {100 * overall['parameters_complete_rate']:.1f}%",
                  "metadata completeness")
    assert_quoted(
        text,
        f"{overall['without_ctdivol_tag']} series that lacked a recorded CTDIvol, "
        f"{overall['n_without_ctdivol_reconstructable']} "
        f"({100 * overall['conditional_reconstructability_rate']:.1f}%)",
        "conditional rate",
    )
    assert_quoted(
        text,
        f"{overall['surveyable']} of {total} series "
        f"({100 * overall['surveyable_rate']:.1f}%)",
        "surveyable",
    )
    assert_quoted(
        text,
        f"{total - overall['surveyable']} ({100 * (1 - overall['surveyable_rate']):.1f}%)",
        "unsurveyable",
    )
    assert_quoted(
        text,
        f"{overall['n_parameters_complete_but_scanner_uncovered']} series were described "
        f"completely enough to reconstruct but sat on a scanner",
        "coverage-limited series",
    )
    res = overall["model_resolution"]
    assert_quoted(text, f"resolved {res['resolved']} model names", "models resolved")
    assert_quoted(text, f"found {res['ambiguous']} ambiguous", "models ambiguous")
    assert_quoted(text, f"did not cover {res['unknown']}", "models uncovered")
    assert overall["with_dlp_record"] == 0
    assert "No series carried a usable DLP record" in text


def test_table_1_survived_the_condensation_intact(text, survey):
    for block in survey["tag_absence"]["by_vendor"].values():
        rate = 100 * (block["conditional_reconstructability_rate"] or 0.0)
        assert_quoted(text, f"| **{rate:.1f}%** |", "a Table 1 conditional rate")


def test_accuracy_numbers_are_quoted_correctly(text, survey):
    from ctdose_core.ctdi_table import covers

    fb = survey["fallback_error"]
    rows = [r for r in survey["series"] if not r["error"]]
    has = [r for r in rows if r["has_ctdivol_tag"]]
    covered = [r for r in has if covers(r.get("manufacturer_raw", ""), r["model_name"] or "")]
    eligible = [r for r in covered if r["fallback_feasible"]]
    assert len(eligible) == fb["n_eligible"] == fb["n_evaluated"]

    assert_quoted(text, f"Of the {len(has)} screened series", "n with a recorded CTDIvol")
    assert_quoted(text, f"{len(covered)} sat on a scanner in the coefficient table", "covered")
    assert_quoted(text, f"{len(eligible)} of those also had complete acquisition parameters",
                  "eligible")
    assert_quoted(text, f"All {fb['n_evaluated']} were evaluated", "all evaluated")

    n_distinct = len({
        (round(c["recorded_ctdivol_mgy"], 6), round(c["estimated_ctdivol_mgy"], 6))
        for c in survey["fallback_cases"]
    })
    assert_quoted(text, f"comprise {n_distinct} distinct", "distinct pairs")

    by_vendor = fb["by_vendor"]
    assert "GE" not in by_vendor
    quoted = ", ".join(f"{v} {by_vendor[v]}" for v in ("Canon/Toshiba", "Siemens", "Philips"))
    assert_quoted(text, f"**{quoted}, GE 0**", "accuracy-arm vendor breakdown")

    ge = survey["tag_absence"]["by_vendor"]["GE"]
    assert ge["with_ctdivol_tag"] == 0
    assert_quoted(text, f"({100 * ge['ctdivol_absence_rate']:.1f}% absence)",
                  "the reason GE contributes nothing")
    assert "unvalidated on GE" in text, "the GE caveat must survive into the Limitations"

    assert_quoted(text, f"median relative error was {_signed(fb['overall']['median'])}",
                  "pooled median")
    assert_quoted(text, f"mean absolute error of {fb['overall']['mean_absolute']:.1f}%",
                  "pooled MAE")

    h = fb["diagnostic_and_collimation_measured"]
    assert_quoted(text, f"n = {h['n']}", "headline n")
    assert_quoted(text, f"median relative error {_signed(h['median'])}", "headline median")
    assert_quoted(text, f"mean absolute error {h['mean_absolute']:.1f}%", "headline MAE")
    assert_quoted(text, f"{100 * h['within_20_percent']:.1f}% of cases within ±20%", "within 20")


def test_the_uncertainty_on_the_headline_claim_is_quoted_correctly(text):
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
    assert_quoted(text, rf"$\chi^2$({v['dof']}) = {v['chi2']:.1f}", "chi-squared")
    assert_quoted(text, f"Cramér's V = {v['cramers_v']:.2f}", "Cramer's V")
    assert "nominal p < 0.001" in text and "may not be independent" in text
    assert "may underestimate uncertainty" in text

    cav = stats["independence_caveat"]
    distinct = cav["headline_n"] - cav["headline_n_duplicate_pairs"]
    assert_quoted(text, f"of the {cav['headline_n']}, {distinct} are distinct", "distinctness")
    assert_quoted(text, f"from {cav['headline_n_collections']} collections", "collection count")


# --- the supplement's numbers ------------------------------------------------------------


def test_the_supplements_stratified_detail_is_quoted_correctly(supplement, survey):
    fb = survey["fallback_error"]
    h = fb["diagnostic_and_collimation_measured"]
    assert_quoted(supplement,
                  f"interquartile range of {_signed(h['p25'])} to {_signed(h['p75'])}",
                  "headline IQR")
    assert_quoted(supplement, f"full range of {_signed(h['min'])} to {_signed(h['max'])}",
                  "headline range")
    assert_quoted(supplement, f"{100 * h['within_50_percent']:.1f}% of cases within ±50%",
                  "within 50")
    for vendor, d in h["by_vendor"].items():
        assert d["n"] > 0
        assert_quoted(supplement, f"{vendor} (n = {d['n']}) {_signed(d['median'])}",
                      f"{vendor} headline median")

    strata = fb["by_collimation_provenance"]
    measured, reconstructed = strata["measured"], strata["reconstructed"]
    assert_quoted(supplement, f"{reconstructed['n']} cases had to rebuild the collimation",
                  "reconstructed stratum n")
    assert_quoted(
        supplement,
        f"median error of {_signed(reconstructed['median'])} against "
        f"{_signed(measured['median'])} for the {measured['n']}",
        "collimation strata medians",
    )
    counts = fb["vendor_by_collimation_provenance"]
    siemens, canon, philips = counts["Siemens"], counts["Canon/Toshiba"], counts["Philips"]
    assert siemens["collimation_reconstructed"] == philips["collimation_reconstructed"] == 0
    assert_quoted(
        supplement,
        f"Siemens contributed {siemens['collimation_measured']} with a recorded collimation "
        f"and none without",
        "Siemens split",
    )
    assert_quoted(
        supplement,
        f"Canon/Toshiba {canon['collimation_measured']} with and "
        f"{canon['collimation_reconstructed']} without",
        "Canon split",
    )
    assert_quoted(supplement, f"Philips {philips['collimation_measured']} with and none without",
                  "Philips split")


def test_the_supplements_uncovered_model_names_are_quoted_correctly(supplement, survey):
    unmatched = list(survey["tag_absence"]["unmatched_models"].items())
    name, count = unmatched[0]
    assert_quoted(supplement, f"`{name}` ({count} series)", "commonest uncovered model")
    for name, count in unmatched[1:4]:
        assert_quoted(supplement, f"`{name}` ({count})", f"uncovered model {name}")


def test_the_supplements_size_and_dose_numbers_are_quoted_correctly(supplement, size_and_dose):
    s = size_and_dose["summary"]
    p = s["patient_like"]
    assert_quoted(supplement, f"Of the {s['n_cases_in_survey']}, one could not be contoured",
                  "contour failure")
    assert_quoted(supplement, f"leaving {s['n_evaluated']}", "n evaluated")
    assert_quoted(supplement, f"{s['n_phantom_collection']} scans of physical test objects",
                  "phantoms")
    assert_quoted(supplement, f"{s['n_too_few_slices']} series of fewer than 10 slices",
                  "short series")
    assert_quoted(supplement, f"**{s['n_patient_like']} patient-like series**", "patient-like n")

    wed, factor, ssde_ = (p["water_equivalent_diameter_cm"], p["ssde_conversion_factor"],
                          p["ssde_from_recorded_mgy"])
    assert_quoted(supplement, f"median of {wed['median']:.1f} cm", "WED median")
    assert_quoted(supplement, f"{wed['p25']:.1f}–{wed['p75']:.1f} cm", "WED IQR")
    assert_quoted(supplement, f"range {wed['min']:.1f}–{wed['max']:.1f} cm", "WED range")
    assert_quoted(supplement, f"median of {factor['median']:.2f}", "factor median")
    assert_quoted(supplement, f"median of {ssde_['median']:.1f} mGy", "SSDE median")

    e = p["effective_dose_msv"]
    assert e["n"] == s["n_patient_like_with_scan_range"]
    assert_quoted(supplement, f"**{e['n']} of the {s['n_patient_like']}**", "E denominator")
    assert_quoted(supplement, f"median of {e['median']:.1f} mSv", "E median")
    assert_quoted(supplement, f"range {e['min']:.1f}–{e['max']:.1f} mSv", "E range")
    assert "identical longitudinal position" in supplement


# --- the review-facing builds -------------------------------------------------------------


def test_the_full_manuscript_was_preserved_unchanged():
    """The Technical Note is a second artefact, not a replacement.

    The full Research Article is the complete record and is already public and archived;
    condensing it must not consume it. It still carries the organ layer, which is correct:
    what the revision withdraws is publication in JACMP, not the work.
    """
    assert FULL.exists(), "the full manuscript must survive the reformat"
    assert "**Article type:** Research Article" in FULL.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "letter", ["response_to_editor_jacmp_r1.md", "cover_letter_jacmp_r1.md"]
)
def test_the_revision_letters_quote_the_real_counts(text, letter):
    """A letter telling the editor the manuscript is N words must mean this manuscript."""
    path = PAPER / letter
    if not path.exists():
        pytest.skip(f"{letter} not written")
    body = path.read_text(encoding="utf-8")

    words = len(text.split())
    assert str(words) in body, f"{letter} does not quote the Technical Note's {words} words"
    assert str(len(FULL.read_text(encoding='utf-8').split())) in body, (
        f"{letter}'s figure for the previous version is stale"
    )


@pytest.mark.parametrize(
    "name",
    [
        "ctdose-core_jacmp_technote_masked.pdf",
        "ctdose-core_jacmp_technote_supplement.pdf",
        "ctdose-core_jacmp_technote_tracked_changes.pdf",
        "response_to_editor_jacmp_r1.pdf",
    ],
)
def test_every_review_facing_pdf_names_nobody(name):
    """One leak in any of the four defeats the double-masked review."""
    pdf = PAPER / name
    if not pdf.exists():
        pytest.skip(f"{name} not built")
    pytest.importorskip("pypdf", reason="pypdf is needed to read the PDF back")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_masked_manuscript", REPO / "tools" / "build_masked_manuscript.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not module.verify(pdf), f"{name} identifies the author"


@pytest.mark.parametrize(
    ("built", "source"),
    [
        ("ctdose-core_jacmp_technote.pdf", "ctdose-core_jacmp_technote.md"),
        ("ctdose-core_jacmp_technote_masked.pdf", "ctdose-core_jacmp_technote.md"),
        ("ctdose-core_jacmp_technote_supplement.pdf",
         "ctdose-core_jacmp_technote_supplement.md"),
        ("ctdose-core_jacmp_technote_tracked_changes.pdf",
         "ctdose-core_jacmp_technote_masked.md"),
        ("title_page_jacmp.pdf", "title_page_jacmp.md"),
    ],
)
def test_no_built_pdf_is_older_than_its_source(built, source):
    """A stale build is the one that gets uploaded by accident."""
    pdf, src = PAPER / built, PAPER / source
    if not pdf.exists():
        pytest.skip(f"{built} not built")
    assert pdf.stat().st_mtime >= src.stat().st_mtime, (
        f"paper/{built} is older than paper/{source}; rebuild it"
    )


def test_the_source_contains_no_character_the_body_font_cannot_draw(text, supplement):
    pytest.importorskip("reportlab", reason="reportlab is needed to build the PDF")
    import sys

    sys.path.insert(0, str(REPO / "tools"))
    from build_manuscript_pdf import assert_encodable

    assert_encodable(text)
    assert_encodable(supplement)
