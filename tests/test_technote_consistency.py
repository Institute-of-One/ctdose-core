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
#:
#: 5000, from Table 3 of the journal's own Author Guidelines, read on 2026-11-08:
#: "Word limits account for all elements of the manuscript, including the title page,
#: abstract, main text, figure and table legends, appendices, and references." This
#: constant was 3000 for the whole of the first revision, which is the *Case Report*
#: limit in the same table, not the Technical Note's. The manuscript was therefore
#: condensed to 2972 words against a budget it had 2028 words of room inside, and the
#: review that came back asks for definitions, a reorganisation and a whole validation
#: arm -- none of which would have fitted under the limit that was being enforced.
#: A limit read off the wrong row of the right table is the expensive kind of error.
WORD_LIMIT = 5000

#: The rest of Table 3's row for a Technical Note, pinned for the same reason.
FIGURE_AND_TABLE_LIMIT = 6
REFERENCE_LIMIT = 20

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


#: Small numbers read better spelled out in a letter, so a gate that demands the numeral
#: forces prose to be worse to satisfy it. Accept either form.
_SPELLED = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty",
}


def _states(body: str, value: int) -> bool:
    """Whether the text gives this count, as a numeral or spelled out."""
    if re.search(rf"\b{value}\b", body):
        return True
    word = _SPELLED.get(value)
    return bool(word and re.search(rf"\b{word}\b", body, flags=re.IGNORECASE))


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


def test_the_display_items_are_within_the_technical_note_limit(text):
    """Figures plus tables, against Table 3's limit of six for a Technical Note.

    Only the word limit was ever checked, and it was checked against the wrong row.
    The other two numbers in the same row went unenforced, so they are gates now:
    a display item or a reference added during a revision cannot push past the limit
    unnoticed.
    """
    figures = sorted(set(re.findall(r"\*\*Figure (\d)\.\*\*", text)), key=int)
    tables = sorted(set(re.findall(r"\*\*Table (\d)\.\*\*", text)), key=int)
    assert figures == [str(i) for i in range(1, len(figures) + 1)], (
        f"main-text figures are numbered {figures}; expected 1..n with no gap"
    )
    assert tables == [str(i) for i in range(1, len(tables) + 1)], (
        f"main-text tables are numbered {tables}; expected 1..n with no gap"
    )
    assert len(figures) + len(tables) <= FIGURE_AND_TABLE_LIMIT, (
        f"{len(figures)} figures + {len(tables)} tables exceeds the Technical Note "
        f"limit of {FIGURE_AND_TABLE_LIMIT}"
    )
    for n in figures:
        matches = list((PAPER / "figures").glob(f"fig{n}_*.png"))
        assert matches, f"no image file for Figure {n} in paper/figures"


def test_the_reference_count_is_within_the_technical_note_limit(text):
    """Table 3 allows twenty references for a Technical Note."""
    refs = re.findall(r"^(\d+)\. ", text, flags=re.MULTILINE)
    assert len(refs) <= REFERENCE_LIMIT, (
        f"the Technical Note lists {len(refs)} references; the limit is "
        f"{REFERENCE_LIMIT}"
    )


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


def test_the_structured_abstract_uses_the_headings_the_journal_specifies(text):
    """"Background, Purpose, Methods, Results, and Conclusions", per the guidelines.

    The last was "Conclusion" here for two submissions. The journal's own Author
    Guidelines give the Technical Note abstract as "Background, Purpose, Methods,
    Results, and Conclusions", so the manuscript was right to change and this test was
    wrong to require the singular: where a test and the journal disagree, the journal is
    correct.
    """
    abstract = text[text.index("## Abstract"):text.index("\n---\n", text.index("## Abstract"))]
    headings = re.findall(r"\*\*(\w+):\*\*", abstract)
    assert headings == ["Background", "Purpose", "Methods", "Results", "Conclusions"]
    # Max 500 words, from the same row of the same table as the other limits.
    assert len(abstract.split()) <= 500, (
        f"the structured abstract is {len(abstract.split())} words; the limit is 500"
    )


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
        f"declared in {counts['files_matching']} of {counts['files_declaring_a_count']}",
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
    # The accuracy arm drawn from the archive still has no GE case, and the manuscript
    # must point the reader at where GE is addressed instead of leaving the zero bare.
    assert_quoted(text, "§3.4 addresses GE separately", "the pointer to the GE arm")

    assert_quoted(text, f"median relative error was {_signed(fb['overall']['median'])}",
                  "pooled median")
    assert_quoted(text, f"mean absolute error of {fb['overall']['mean_absolute']:.1f}%",
                  "pooled MAE")

    h = fb["diagnostic_and_collimation_measured"]
    assert_quoted(text, f"n = {h['n']}", "headline n")
    assert_quoted(text, f"median relative error {_signed(h['median'])}", "headline median")
    assert_quoted(text, f"mean absolute error {h['mean_absolute']:.1f}%", "headline MAE")
    assert_quoted(text, f"{100 * h['within_20_percent']:.1f}% of cases within ±20%", "within 20")


def test_the_ge_arm_numbers_are_quoted_correctly(text):
    """§3.4 and Table 2 against results/ge_rdsr_accuracy.json, value by value.

    The Section Editor asked for phantom scanning or a database carrying RDSR for GE.
    This is the answer, so every figure in it is resolved from the result file rather
    than from the sentence that was easiest to write.
    """
    path = RESULTS / "ge_rdsr_accuracy.json"
    if not path.exists():
        pytest.skip("the GE arm has not been run; see tools/evaluate_ge_rdsr.py")
    payload = _load(path)
    summary, cases = payload["summary"], payload["cases"]

    assert_quoted(text, f"hold {summary['n_events_seen']} irradiation events", "GE events")
    assert_quoted(
        text, f"{summary['n_events_with_ctdivol']} of which record a CTDIvol", "with CTDIvol"
    )
    reasons = summary["exclusions_by_reason"]
    no_feed = next(n for r, n in reasons.items() if "no table feed" in r)
    refused = reasons["scanner not in the open coefficient database"]
    assert_quoted(text, f"{no_feed} of those 15 were excluded", "no-table-feed exclusions")
    assert_quoted(text, f"and {refused} because their scanner", "model refusals")
    assert_quoted(text, f"{summary['n_evaluated']} events from 2 scanner models", "evaluated")

    errors = [c["percent_error"] for c in cases]
    assert_quoted(text, f"median of {_signed(summary['overall']['median'])}", "GE median")
    assert_quoted(
        text,
        f"range of {_signed(min(errors))} to {_signed(max(errors))}",
        "GE error range",
    )
    assert summary["overall"]["within_20_percent"] == 1.0
    assert_quoted(text, "within 20% of the value the scanner recorded", "within 20")

    implied = summary["implied_coefficient_mgy_per_100mas"]
    assert_quoted(
        text,
        f"{implied['min']:.2f} to {implied['max']:.2f} mGy per 100 mAs",
        "implied coefficient band",
    )

    # Table 2: every row's four numbers must be the computed ones.
    for case in cases:
        row = (
            f"| {case['mas_per_rotation']:.1f} | {case['pitch']:.2f} | "
            f"{case['recorded_ctdivol_mgy']:.2f} | "
            f"{case['estimated_ctdivol_mgy']:.2f} | "
            f"**{_signed(case['percent_error'])}**"
        )
        assert row in text, f"Table 2 is missing or misquotes the row {row!r}"

    # The sensitivity must be named as one, not adopted.
    sensitivity = summary["collimation_sensitivity"]["by_collimation_mm"]["20mm"]
    assert_quoted(
        text, f"median error of {_signed(sensitivity['median'])}", "20 mm sensitivity"
    )
    assert "not adopted" in text, (
        "the better-agreeing collimation row must be reported as a sensitivity and "
        "explicitly not adopted"
    )


def test_the_manuscript_separates_the_dose_model_from_the_recovery_path(text):
    """The GE arm validates the dose model, not recovery from a de-identified header.

    Those are different claims, and the result file says so in its provenance. If the
    manuscript merges them it claims GE validation it does not have, which is the exact
    gap the reviewers raised. The denial has to be in the body and in the Limitations.
    """
    assert "does **not** test recovery from a de-identified header" in text, (
        "§2.7 must state which claim the GE arm does not support"
    )
    assert "not independent patients" in text, (
        "the GE events come several to a study and the text must say so"
    )
    limitations = text[text.index("**Limitations.**"):]
    assert "validating the dose model and not recovery from a de-identified header" in (
        limitations
    ), "the Limitations must repeat the separation, not only the Methods"
    assert "remains unvalidated against a header-derived reference" in limitations, (
        "the GE conditional rate in Table 1 still has no header-derived reference"
    )


def test_the_two_sided_error_claim_replaced_the_conservative_underestimate(text):
    """Reviewer 1: Figure 3A shows over-estimates, so "underestimate" was not supported.

    The claim was in two places, the Methods and the Limitations, and both had to go.
    What replaces it is a count, taken from the result file.
    """
    assert "conservative underestimate" not in text, (
        "the withdrawn claim is still in the manuscript"
    )
    fb = _load(RESULTS / "open_fallback.json")
    cases = fb["cases"]
    over = [c for c in cases if c["percent_error"] > 0]
    under = [c for c in cases if c["percent_error"] < 0]
    assert_quoted(
        text,
        f"over-estimated the recorded CTDIvol in {len(over)} of the {len(cases)} cases "
        f"and under-estimated it in {len(under)}",
        "the two-sided count",
    )
    worst = max(cases, key=lambda c: c["percent_error"])
    assert_quoted(
        text,
        f"reconstructed {worst['estimated_ctdivol_mgy']:.2f} mGy where the scanner "
        f"recorded {worst['recorded_ctdivol_mgy']:.2f} mGy",
        "the largest over-estimate",
    )
    # And nothing may describe a reconstructed value as a bound.
    for claim in ("is a lower bound", "are underestimates of unquantified size"):
        assert claim not in text, f"{claim!r} contradicts the two-sided result"


def test_the_k_factor_is_attributed_to_aapm_96_not_icrp_103(text):
    """Reviewer 1, line 65: "k-factors are not described in ICRP 103."

    They are AAPM Report 96 Table 3, on ICRP 60 weighting. The manuscript has to name
    the right document and say which weighting the reported effective doses carry,
    because ICRP 103 revised several of the factors.
    """
    assert "AAPM Report 96, Table 3" in text, "the k-factor needs its real source"
    assert "ICRP Publication 60 tissue weighting" in text, (
        "the manuscript must say which weighting generation the coefficients rest on"
    )
    assert "not an ICRP 103 quantity" in text
    assert re.search(r"AAPM Report No\. 96\b.*2008", text), (
        "Report 96 must be in the reference list"
    )
    assert "Deak" in text, "the ICRP 103 alternative coefficients must be cited"
    # The old wording must not survive anywhere.
    assert "conversion factor of ICRP Publication 103" not in text


def test_the_bowtie_limitation_is_quantified(text):
    """Reviewer 1, line 74: bowtie varies by patient and study, and affects nCTDIw.

    A limitation that says it could matter is weaker than one that says how much, and
    the table measures several filters for the same technique, so the figure is
    available rather than assumed.
    """
    limitations = text[text.index("**Limitations.**"):]
    for phrase in ("pediatric", "cardiac", "differently sized bodies"):
        assert phrase in limitations, f"the bowtie limitation does not mention {phrase}"
    assert "605 comparisons" in limitations and "median of 12.2%" in limitations, (
        "the bowtie sensitivity must be quantified from the coefficient table"
    )


def test_the_reviewers_undefined_terms_are_defined(text):
    """Reviewer 1's minor comments, each a term used without a definition.

    Keyed to the lines of the reviewed manuscript. The point is not that a particular
    word was removed but that the manuscript now says what it means.
    """
    # L51: the four provenance labels, defined explicitly.
    for label in ("**recorded**", "**reconstructed**", "**derived**", "**unrecoverable**"):
        assert label in text, f"{label} is used as a provenance level but never defined"
    # L53: what keeping the lower-ranked candidates means.
    assert "The lower-ranked candidates are not discarded" in text
    assert "losers" not in text, "the metaphor the reviewer asked about is still there"
    # L70: row selection, said to be from the coefficient table.
    assert "Selecting a row from the coefficient table of Dinwiddie" in text
    # L104: what makes a series recoverable.
    assert "A series is counted as **dose-recoverable** if" in text
    # L43: "good" meaning accurate.
    assert "only as accurate as its metadata" in text
    # L85 "here", L86 jargon.
    assert "no stale or hand-edited figure passes continuous integration" not in text, (
        "the undefined jargon is still there"
    )
    assert "Every number reported in this note" in text
    # L118, L142.
    assert "present in nearly every series" in text
    assert "Availability of a recorded CTDIvol was strongly associated" in text


def test_the_manuscript_is_written_in_the_first_person_singular(text):
    """One author, so one voice.

    The authorial "we" spreads one person's choices, judgements and labelling over a
    team that does not exist, and the reviewers' objections are precisely to those
    choices. Quoted reviewer text is not covered by this; the manuscript is.
    """
    stray = re.findall(r"\b(we|our|us)\b", text, flags=re.IGNORECASE)
    assert not stray, f"authorial plural in a single-author manuscript: {stray}"
    assert "to my knowledge" in text


#: British spellings and their US forms. A named list, not a pattern over "-ised":
#: "revised", "advised", "supervised" and "comprised" are US English too, and a greedy
#: rule flags them. A gate that cries wolf is a gate someone switches off.
#: The -ise/-isation stems that occur in this field, checked with their inflections.
#: Doing it by stem rather than by whole word is what catches the inflection nobody
#: thought of: the first pass of this gate listed "modelling" and missed "modelled",
#: and listed "analyse" and missed "analysing". Both were in the supplement.
BRITISH_STEMS = (
    "optimis", "normalis", "anonymis", "emphasis", "summaris", "conceptualis",
    "visualis", "organis", "recognis", "characteris", "standardis", "minimis",
    "maximis", "utilis", "prioritis", "parameteris", "digitis", "realis",
    "generalis", "specialis", "discretis", "quantis", "analys", "catalys", "paralys",
)
#: Inflections to try after each stem. The bare stem is not a word, so it is excluded.
BRITISH_ENDINGS = ("e", "ed", "es", "ing", "ation", "ations", "er", "ers")

#: Pairs that are not an -ise/-ize matter.
US_SPELLING = {
    "licence": "license",
    "defence": "defense",
    "offence": "offense",
    "centre": "center",
    "metre": "meter",
    "litre": "liter",
    "fibre": "fiber",
    "paediatric": "pediatric",
    "foetal": "fetal",
    "oedema": "edema",
    "anaemia": "anemia",
    "colour": "color",
    "behaviour": "behavior",
    "favour": "favor",
    "modelling": "modeling",
    "modelled": "modeled",
    "labelling": "labeling",
    "labelled": "labeled",
    "signalled": "signaled",
    "judgement": "judgment",
    "acknowledgement": "acknowledgment",
    "ageing": "aging",
    "whilst": "while",
    "amongst": "among",
    "learnt": "learned",
}


def test_the_manuscript_uses_us_spelling(text, supplement):
    """Reviewer 3 asked for one instance; the journal is American throughout.

    Checked in the supplement too, which is uploaded alongside and had its own copy of
    the word the reviewer named.
    """
    # The masked manuscript and the tracked-changes file are what get uploaded, and each
    # is built by a script holding its own copy of the declarations -- the masking script
    # still said "MIT licence" after the manuscript said "License". A gate that reads
    # only the canonical source would not have seen it.
    documents = [("manuscript", text), ("supplement", supplement)]
    for name in (
        "ctdose-core_jacmp_technote_masked.md",
        "ctdose-core_jacmp_technote_tracked_changes.md",
    ):
        path = PAPER / name
        if path.exists():
            body = path.read_text(encoding="utf-8")
            if name.endswith("tracked_changes.md"):
                # Struck-through text quotes the superseded wording on purpose.
                body = re.sub(r"~~.*?~~", "", body, flags=re.DOTALL)
            documents.append((name, body))

    for name, document in documents:
        found = {
            british: american
            for british, american in US_SPELLING.items()
            if re.search(rf"\b{british}\b", document, flags=re.IGNORECASE)
        }
        for stem in BRITISH_STEMS:
            pattern = rf"\b{stem}(?:{'|'.join(BRITISH_ENDINGS)})\b"
            for match in re.finditer(pattern, document, flags=re.IGNORECASE):
                word = match.group(0)
                found[word] = word.lower().replace("is", "iz", 1)
        assert not found, (
            f"British spellings in the {name}: "
            + ", ".join(f"{b} -> {a}" for b, a in sorted(found.items()))
        )


def test_the_title_page_describes_the_supplement_that_exists(text, supplement):
    """The title page is uploaded, and nothing was reading what it claims.

    It said the supplementary file contains "the optional organ-dose layer, the detailed
    stratified analyses and Figures S1-S3". The organ layer was withdrawn from this
    submission in revision 1 and the supplement has never had a figure, so two of the
    three were false for two submissions. The declarations on it had also kept the
    British spellings the manuscript has now dropped.
    """
    page = (PAPER / "title_page_jacmp.md").read_text(encoding="utf-8")

    # Figures: the claim must match the file.
    supplement_figures = re.findall(r"\*\*Figure S\d", supplement)
    if not supplement_figures:
        assert "Figures S1" not in page and "Figure S1" not in page, (
            "the title page announces supplementary figures the supplement does not have"
        )
        assert "contains no figures" in page, (
            "the title page should say the supplement has no figures, so a reader is "
            "not left looking for them"
        )

    # The withdrawn organ layer must not be announced anywhere on an uploaded document.
    for term in ORGAN_TERMS:
        assert term.lower() not in page.lower(), (
            f"the title page announces {term!r}, which was withdrawn from this submission"
        )

    # Every section the supplement actually has should be discoverable from the page.
    headings = re.findall(r"^### S\d+\.\d+ (.+)$", supplement, flags=re.MULTILINE)
    assert headings, "the supplement has no subsections to describe"
    assert "GE validation arm" in page, (
        "the GE arm is the Section Editor's request and must be announced"
    )

    # And the page must agree with the manuscript on the type and keywords.
    assert "**Article type:** Technical Note" in page
    keywords = re.search(r"\*\*Keywords:\*\* (.+)", text).group(1)
    assert keywords in page, "the title page and the manuscript disagree on the keywords"


def test_the_title_page_uses_us_spelling():
    """It is uploaded alongside the manuscript and had its own copies of the wording."""
    page = (PAPER / "title_page_jacmp.md").read_text(encoding="utf-8")
    found = sorted(
        british
        for british in US_SPELLING
        if re.search(rf"\b{british}\b", page, flags=re.IGNORECASE)
    )
    for stem in BRITISH_STEMS:
        pattern = rf"\b{stem}(?:{'|'.join(BRITISH_ENDINGS)})\b"
        found += [m.group(0) for m in re.finditer(pattern, page, flags=re.IGNORECASE)]
    assert not found, f"British spellings on the title page: {sorted(set(found))}"


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


#: The round being submitted. The letters of earlier rounds stay in paper/ as the record
#: of what was told to the editor then, and are NOT checked against the current
#: manuscript: the r1 letters correctly describe the r1 manuscript, and holding them
#: against this one would force the history to be rewritten. What must be true is that
#: the letters for the round being submitted describe the manuscript being submitted.
CURRENT_ROUND = "r2"
CURRENT_LETTERS = [
    f"response_to_editor_jacmp_{CURRENT_ROUND}.md",
    f"cover_letter_jacmp_{CURRENT_ROUND}.md",
]


def _private(name: str) -> str:
    """Read a correspondence file, or skip.

    The cover letters and the point-by-point responses are gitignored on purpose: they
    are correspondence with an editor, not part of the public repository. CI therefore
    runs on a clone that does not have them, and a gate that asserted their presence
    would fail for everyone who did nothing wrong. Absent means "cannot check here";
    present means checked strictly.
    """
    path = PAPER / name
    if not path.is_file():
        pytest.skip(f"{name} is not in this checkout (correspondence is not public)")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("letter", CURRENT_LETTERS)
def test_the_revision_letters_quote_the_real_counts(text, letter):
    """A letter telling the editor the manuscript is N words must mean this manuscript.

    The runbook's rule is that a revision's letter is left over from the previous
    submission more often than not, so the counts it quotes are the ones to check.
    """
    body = _private(letter)

    words = len(text.split())
    assert str(words) in body, f"{letter} does not quote the Technical Note's {words} words"


@pytest.mark.parametrize("letter", CURRENT_LETTERS)
def test_the_current_letters_quote_the_limits_from_the_guidelines(text, letter):
    """The limits a letter asserts must be the journal's, and the counts must be real.

    This revision exists partly because the previous one was written to the wrong limit
    and the letter asserted that wrong limit back to the editor. So the letter's figures
    for the limits are resolved from the same constants the manuscript is checked
    against, and its figures for the manuscript from the manuscript.
    """
    body = _private(letter)
    figures = len(set(re.findall(r"\*\*Figure (\d)\.\*\*", text)))
    tables = len(set(re.findall(r"\*\*Table (\d)\.\*\*", text)))
    refs = len(re.findall(r"^(\d+)\. ", text, flags=re.MULTILINE))

    for value, what in (
        (len(text.split()), "the manuscript's word count"),
        (figures, "the figure count"),
        (tables, "the table count"),
        (refs, "the reference count"),
        (WORD_LIMIT, "the word limit"),
        (REFERENCE_LIMIT, "the reference limit"),
        (FIGURE_AND_TABLE_LIMIT, "the display-item limit"),
    ):
        assert _states(body, value), f"{letter} does not quote {what} ({value})"

    # A second needle for the same fact, bound to the count it limits.
    #
    # This letter states the word limit twice -- once in the correction explaining that
    # the previous revision used the Case Report row, and once in the summary of counts
    # against limits -- so a check for "is 5000 anywhere in the letter" stayed green
    # when the summary was changed to 3000. Where a fact is written in two places, take
    # two needles. Each limit is therefore required in the same passage as the count it
    # governs, which works whether the letter says it in prose or in a table row.
    passages = [p for p in re.split(r"(?<=[.])\s|\n", body) if p.strip()]
    for count, limit, what in (
        (len(text.split()), WORD_LIMIT, "the word count against the word limit"),
        (refs, REFERENCE_LIMIT, "the reference count against the reference limit"),
    ):
        together = [
            p for p in passages if _states(p, count) and "limit" in p.lower()
        ]
        assert together, f"{letter} never states {what}"
        assert any(_states(p, limit) for p in together), (
            f"{letter} states {what} with the wrong limit; it should be {limit}. "
            f"Passage(s): {[p.strip()[:120] for p in together]}"
        )


def test_the_previous_rounds_letters_are_kept_as_the_record(text):
    """The r1 letters must still exist, and must not have been edited to match this one.

    They are what was told to the editor in July. Updating them to agree with the
    current manuscript would destroy the record of the round they belong to -- and
    "2972 words" appearing in an r1 letter is correct, not stale.
    """
    for name in ("response_to_editor_jacmp_r1.md", "cover_letter_jacmp_r1.md"):
        body = _private(name)
        assert "2972 words" in body, (
            f"{name} no longer quotes the word count of the manuscript it accompanied; "
            f"it appears to have been edited to match a later revision"
        )
        assert str(len(text.split())) not in body, (
            f"{name} quotes this revision's word count, so it has been edited; the "
            f"letters of a past round are history and are not updated"
        )


@pytest.mark.parametrize(
    "name",
    [
        "ctdose-core_jacmp_technote_masked.pdf",
        "ctdose-core_jacmp_technote_masked_r1.md",
        "ctdose-core_jacmp_technote_supplement.pdf",
        "ctdose-core_jacmp_technote_tracked_changes.pdf",
        "response_to_editor_jacmp_r1.pdf",
        f"response_to_editor_jacmp_{CURRENT_ROUND}.pdf",
    ],
)
def test_every_review_facing_pdf_names_nobody(name):
    """One leak in any of these defeats the double-masked review.

    The list has to grow with the submission. For revision 2 that means the new
    point-by-point response, and the frozen r1 masked manuscript that the
    tracked-changes diff is built from -- which is a review-facing input even though it
    is not itself uploaded, because anything identifying in it would reach the diff.
    """
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
