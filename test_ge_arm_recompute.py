"""Re-derive every reported number of the GE validation arm from its own rows.

Standard ``unittest``, standard library only, and at the repository root on purpose.
The AI-Check framework runs ``python -I -m unittest discover -s .`` inside a snapshot of
the listed files: isolated mode ignores user site-packages, so numpy, scipy and pydicom
are not importable there, and discovery finds test modules at the snapshot root. The
project's real suite is pytest and stays where it is; this file is a second derivation
that needs nothing but ``json`` and ``statistics``.

**What this file can and cannot catch.** It never imports the project, so an arithmetic
error shared between a rule and the code implementing that rule still fails here. It
cannot check the arm's inputs: the kVp, current, pitch and exposure times are read out of
the RDSR objects by the project's own parser, and this file sees only what that parser
wrote. A misread attribute is therefore invisible to it, and the earlier version of this
docstring claimed an independence it does not have. What it does check is that the
summary agrees with the rows it claims to summarise, that each row follows the stated
formula, and that the beam-width argument rests on a measured quantity rather than on an
identity that would hold whatever the scanner recorded.

That last point is why this file was rewritten on 2026-11-08. Its predecessor "proved"
the beam-width argument by comparing the predicted scan duration at the recorded
collimation against the predicted duration at 40 mm. Those two predictions differ by
exactly 40/recorded, so the assertion could not fail for any input and tested nothing.
The argument now rests on DCM 113824, the event's **total exposure time**, which the
scanner measured, and the test compares that measurement against the duration the
recorded collimation predicts.

What the arm reports, and what is re-derived below:

* five events, reported as a range rather than as one error per event, because the bowtie
  filter is never recorded and the recorded collimation is refuted on four of the five;
* one event whose recorded width its own exposure time corroborates, exact against
  the recorded value, with the bowtie spanning the tabulated filters;
* four whose recorded width it refutes, leaving the collimation undetermined, each
  published with both defensible readings of that;
* an implied coefficient of 8.48 to 8.74 mGy per 100 mAs on the 32 cm events;
* the exclusion accounting, 35 events to 15 with a CTDIvol to 5 compared, under four
  distinct reasons, each count checked against the rows carrying it.
"""

from __future__ import annotations

import json
import statistics
import unittest
from pathlib import Path


#: Walk up from this file to find the result, so it works in the repository and in a
#: snapshot that preserved the relative path.
def _result_file() -> Path:
    here = Path(__file__).resolve()
    for base in (here.parent, *here.parents):
        candidate = base / "results" / "ge_rdsr_accuracy.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("results/ge_rdsr_accuracy.json is not beside this file")


PAYLOAD = json.loads(_result_file().read_text(encoding="utf-8"))
CASES = PAYLOAD["cases"]
SUMMARY = PAYLOAD["summary"]
HEADLINE = SUMMARY["headline"]

#: How far the recorded and measured widths may differ and still be called consistent,
#: restated here so the recomputation does not borrow the number it checks.
#:
#: This decides only whether the recorded width is contradicted, never which tabulated
#: row the arm uses, and the data leave no room for doubt: the four refuted events differ
#: from their measured width by 86 % to 87 % and the corroborated one by 0.6 %, so any
#: line between them gives the same answer. Four earlier versions of the arm used a
#: threshold to *select* a row as well, and every one of them rested on where the line
#: fell; two reviewers refused the last on 2026-11-08.
REFUTATION_MARGIN = 0.25

#: The threshold the arm declares for counting the arithmetic reproduced, restated here
#: rather than read from the output -- reading it from the output and then applying it to
#: the output checks nothing about the threshold.
ARITHMETIC_TOLERANCE_PERCENT = 1.0


def _corroborated(case: dict) -> bool:
    return bool(case["beam_width_consistency"].get("corroborated"))


class TheSummaryAgreesWithItsRows(unittest.TestCase):
    """Recomputed from ``cases``, not read back from ``summary``."""

    def test_the_case_count_is_the_number_of_rows(self):
        self.assertEqual(SUMMARY["n_evaluated"], len(CASES))
        self.assertGreater(len(CASES), 0, "an arm with no rows reports nothing")

    def test_every_published_fraction_is_recomputed(self):
        """within_20_percent and within_50_percent, wherever they appear.

        A reviewer pointed out on 2026-11-08 that the file claimed to re-derive every
        reported number while leaving these alone, so a wrong fraction would have passed.
        """
        point = SUMMARY["point_estimate_under_the_default_assumptions"]
        errors = [c["percent_error"] for c in CASES]
        # Every other field describe() publishes, so that no summary statistic is taken
        # on trust. A reviewer noted twice that the file claimed to re-derive everything
        # while leaving some of these alone.
        self.assertAlmostEqual(point["mean"], statistics.fmean(errors), places=9)
        self.assertAlmostEqual(
            point["mean_absolute"],
            statistics.fmean(abs(e) for e in errors),
            places=9,
        )
        if len(errors) > 1:
            self.assertAlmostEqual(
                point["stdev"], statistics.stdev(errors), places=9
            )
        self.assertEqual(
            SUMMARY["n_exact_table_match"],
            sum(1 for c in CASES if c["exact_table_match"]),
        )
        for field, limit in (("within_20_percent", 20.0), ("within_50_percent", 50.0)):
            with self.subTest(field=field, scope="overall"):
                self.assertAlmostEqual(
                    point[field],
                    sum(1 for e in errors if abs(e) <= limit) / len(errors),
                    places=9,
                )
        per_model = SUMMARY["by_model_point_estimate_under_the_default_assumptions"]
        for model, block in per_model.items():
            rows = [c["percent_error"] for c in CASES if c["table_model"] == model]
            for field, limit in (
                ("within_20_percent", 20.0),
                ("within_50_percent", 50.0),
            ):
                with self.subTest(field=field, model=model):
                    self.assertAlmostEqual(
                        block[field],
                        sum(1 for e in rows if abs(e) <= limit) / len(rows),
                        places=9,
                    )
        # And the envelope's own flags, per case.
        for case in CASES:
            envelope = case["assumption_envelope"]
            cells = [g["percent_error"] for g in envelope["grid"]]
            with self.subTest(case=case["file"], event=case["event_index"]):
                self.assertEqual(
                    envelope["any_within_20_percent"],
                    any(abs(e) <= 20.0 for e in cells),
                )
                self.assertEqual(
                    envelope["all_within_20_percent"],
                    all(abs(e) <= 20.0 for e in cells),
                )

    def test_the_point_estimate_is_recomputed_from_the_rows(self):
        """The point estimate is still published, as one corner of the envelope."""
        errors = [c["percent_error"] for c in CASES]
        point = SUMMARY["point_estimate_under_the_default_assumptions"]
        self.assertAlmostEqual(point["median"], statistics.median(errors), places=9)
        self.assertAlmostEqual(point["min"], min(errors), places=9)
        self.assertAlmostEqual(point["max"], max(errors), places=9)
        self.assertAlmostEqual(point["mean"], statistics.fmean(errors), places=9)
        self.assertEqual(point["n"], len(errors))

    def test_the_pass_condition_is_stated_and_recomputed(self):
        """The arm must name a condition it could fail, under every reading it publishes.

        Two forms of this have been refused. The arm first reported spans and called that
        a test, naming no figure that could have counted against the model. It then asked
        whether the span contained zero, which both reviewers refused on the same round:
        the grid holds discrete cells, so a span straddling zero does not mean any
        admissible combination reproduces the reference, and the condition was applied to
        the wide reading alone, which picks a side between readings the arm says it does
        not choose between.

        The condition is now about the best-agreeing admissible cell, and is evaluated
        per reading.
        """
        self.assertIn("pass_condition", SUMMARY)
        self.assertIn("comes closest", SUMMARY["pass_condition"])
        self.assertIn("every reading", SUMMARY["pass_condition"])

        for case in CASES:
            grid = case["assumption_envelope"]["grid"]
            band = case["reported_band"]
            readings = {"as_reported": band["widths_mm"]}
            narrowed = band.get("also_contradicted_removed")
            if narrowed is not None:
                readings["also_contradicted_removed"] = narrowed.get("widths_mm") or []
            self.assertEqual(
                sorted(case["arithmetic_reproduced"]), sorted(readings)
            )
            for reading, widths in readings.items():
                cells = [g for g in grid if g["collimation_mm"] in widths]
                got = case["arithmetic_reproduced"][reading]
                if not cells:
                    # A reading with no admissible combination is not a pass.
                    with self.subTest(
                        case=case["file"], event=case["event_index"], reading=reading
                    ):
                        self.assertEqual(got["n"], 0)
                        self.assertTrue(got["incompatible"])
                        self.assertFalse(got["within_tolerance"])
                    continue
                best = min(cells, key=lambda g: abs(g["percent_error"]))
                with self.subTest(
                    case=case["file"], event=case["event_index"], reading=reading
                ):
                    self.assertEqual(got["n"], len(cells))
                    self.assertEqual(got["bowtie"], best["bowtie"])
                    self.assertEqual(got["collimation_mm"], best["collimation_mm"])
                    self.assertAlmostEqual(
                        got["percent_error"], best["percent_error"], places=9
                    )
                    # Against the declared threshold, not the one the output carries.
                    # Reading the tolerance from the output and then applying it to the
                    # output checks nothing about the threshold, as a reviewer pointed
                    # out on 2026-11-08.
                    self.assertAlmostEqual(
                        got["tolerance_percent"], ARITHMETIC_TOLERANCE_PERCENT, places=9
                    )
                    self.assertEqual(
                        got["within_tolerance"],
                        abs(best["percent_error"]) <= ARITHMETIC_TOLERANCE_PERCENT,
                    )
                    # The tolerance must not have been widened to admit the result: the
                    # best cell has to clear it with room.
                    self.assertLess(
                        abs(best["percent_error"]),
                        got["tolerance_percent"],
                        "the best admissible cell sits at the tolerance, so the verdict "
                        "turns on where the tolerance was set",
                    )

        aggregate = SUMMARY["arithmetic_reproduced_by_reading"]
        for reading, block in aggregate.items():
            rows = [
                c["arithmetic_reproduced"][reading]
                for c in CASES
                if reading in c["arithmetic_reproduced"]
            ]
            # An incompatible reading counts as not within tolerance, so it cannot be
            # dropped from the aggregate to make the rest look unanimous.
            for row in rows:
                if row.get("incompatible"):
                    self.assertFalse(row["within_tolerance"])
            with self.subTest(reading=reading):
                answered = [r for r in rows if r.get("n")]
                self.assertEqual(block["n_events"], len(rows))
                self.assertEqual(
                    block["n_events_this_reading_can_answer"], len(answered)
                )
                self.assertEqual(
                    block["n_events_incompatible_with_this_reading"],
                    len(rows) - len(answered),
                )
                self.assertEqual(
                    block["all_within_tolerance"],
                    bool(rows) and all(r["within_tolerance"] for r in rows),
                )
                # The worst case must say whether it covers every event, so that a
                # reading unable to answer for some of them cannot look unanimous.
                self.assertEqual(
                    block["worst_case_is_over_all_events"],
                    len(answered) == len(rows),
                )
                if answered:
                    self.assertAlmostEqual(
                        block["worst_best_cell_percent_error"],
                        max(abs(r["percent_error"]) for r in answered),
                        places=9,
                    )
                else:
                    self.assertIsNone(block["worst_best_cell_percent_error"])
        # Every reading any case publishes must appear in the aggregate, so that one
        # cannot be dropped to make the other look unanimous.
        self.assertEqual(
            sorted(aggregate),
            sorted({k for c in CASES for k in c["arithmetic_reproduced"]}),
        )

    def test_the_sample_accounting_matches_the_per_file_inventory(self):
        """n_files and the per-file event totals, against the files block and the rows.

        A reviewer pointed out on 2026-11-08 that the suite checked only the global union
        and the summary totals, so a wrong file count or a wrong per-file event total
        would have passed everything.
        """
        files = PAYLOAD["files"]
        self.assertEqual(SUMMARY["n_files"], len(files))
        self.assertEqual(
            SUMMARY["n_events_seen"], sum(f["n_events"] for f in files)
        )
        self.assertEqual(
            SUMMARY["n_events_with_ctdivol"],
            sum(f["n_events_with_ctdivol"] for f in files),
        )
        # Each file's own events must be the ones attributed to it, and each index must
        # fall inside that file's declared count.
        for entry in files:
            name = entry["file"]
            indices = sorted(
                c["event_index"] for c in CASES if c["file"] == name
            ) + sorted(
                e["event_index"] for e in PAYLOAD["excluded"] if e["file"] == name
            )
            with self.subTest(file=name):
                self.assertEqual(
                    len(indices),
                    entry["n_events"],
                    f"{name} declares {entry['n_events']} events and {len(indices)} are "
                    f"attributed to it",
                )
                self.assertEqual(len(set(indices)), len(indices), "duplicate index")
                for index in indices:
                    self.assertLess(index, entry["n_events"])
                    self.assertGreaterEqual(index, 0)

    def test_the_sample_says_what_it_can_and_cannot_discriminate(self):
        """The pitch division is the sharpest check here, and it is published as such."""
        can = PAYLOAD["provenance"]["what_the_sample_can_discriminate"]
        for key in (
            "the_pitch_division",
            "the_coefficient_lookup",
            "the_collimation_and_filter_choice",
            "an_error_of_a_few_per_cent",
        ):
            self.assertIn(key, can)
        self.assertTrue(can["the_collimation_and_filter_choice"].startswith("No"))
        self.assertTrue(can["an_error_of_a_few_per_cent"].startswith("No"))

        # The coefficient-lookup claim, re-derived rather than read: every implied
        # coefficient must fall inside the range its own model and phantom tabulate.
        # Hand-written figures stood in that note until a reviewer found them wrong.
        for case in CASES:
            values = [
                g["nctdi_w_mgy_per_100mas"] for g in case["assumption_envelope"]["grid"]
            ]
            implied = case["implied_nctdi_w_mgy_per_100mas"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                self.assertGreaterEqual(implied, min(values) - 1e-9)
                self.assertLessEqual(implied, max(values) + 1e-9)

        # And the pitch evidence must hold: two events of the same model and phantom at
        # different pitches must imply the same coefficient, which they cannot do if the
        # pitch division is wrong.
        groups: dict[tuple[str, int], list[dict]] = {}
        for case in CASES:
            groups.setdefault((case["table_model"], case["phantom_cm"]), []).append(case)
        tested = 0
        for (model, phantom), rows in groups.items():
            pitches = {round(c["pitch"], 2) for c in rows}
            if len(pitches) < 2:
                continue
            tested += 1
            implied = [c["implied_nctdi_w_mgy_per_100mas"] for c in rows]
            spread = (max(implied) - min(implied)) / min(implied)
            ratio = max(pitches) / min(pitches)
            with self.subTest(model=model, phantom=phantom):
                self.assertGreater(ratio, 1.2, "the pitches must differ enough to tell")
                self.assertLess(
                    spread,
                    0.05,
                    f"{model} at {phantom} cm implies coefficients spread "
                    f"{100 * spread:.1f} % across pitches differing by {ratio:.2f}x; "
                    f"a mishandled pitch division would separate them by that ratio",
                )
        self.assertGreaterEqual(
            tested, 1, "no model and phantom appears at two pitches, so nothing here "
            "bears on the pitch division"
        )

    def test_every_event_is_accounted_for_exactly_once(self):
        seen = {(c["file"], c["event_index"]) for c in CASES}
        seen |= {(e["file"], e["event_index"]) for e in PAYLOAD["excluded"]}
        self.assertEqual(len(seen), SUMMARY["n_events_seen"])
        self.assertEqual(
            SUMMARY["n_evaluated"] + SUMMARY["n_excluded"], SUMMARY["n_events_seen"]
        )
        self.assertEqual(
            sum(SUMMARY["exclusions_by_reason"].values()), SUMMARY["n_excluded"]
        )

    def test_the_exclusions_are_given_as_distinct_reasons(self):
        """One reason covering every exclusion would hide which rule did the work."""
        reasons = SUMMARY["exclusions_by_reason"]
        self.assertGreaterEqual(len(reasons), 3, f"only {len(reasons)} distinct reasons")
        for reason, count in reasons.items():
            with self.subTest(reason=reason):
                self.assertGreater(count, 0, "a reason with no events is not a reason")
                self.assertGreater(len(reason), 20, "the reason must say why, not label")
        counted = sum(
            1 for e in PAYLOAD["excluded"] if e["reason"] in reasons
        )
        self.assertEqual(counted, SUMMARY["n_excluded"], "an exclusion has no reason row")

        # Each reported count must equal the number of rows carrying that reason. The
        # earlier version checked only that the counts summed to n_excluded and that
        # every row's reason appeared somewhere, which two reasons could satisfy with
        # their counts swapped. A reviewer pointed that out on 2026-11-08.
        from collections import Counter

        actual = Counter(e["reason"] for e in PAYLOAD["excluded"])
        self.assertEqual(
            dict(actual),
            dict(reasons),
            "the reported exclusion counts do not match the exclusion rows",
        )

    def test_the_events_with_a_recorded_ctdivol_are_counted_correctly(self):
        no_reference = sum(
            1 for e in PAYLOAD["excluded"] if "no recorded CTDIvol" in e["reason"]
        )
        self.assertEqual(
            SUMMARY["n_events_with_ctdivol"],
            SUMMARY["n_events_seen"] - no_reference,
        )


class ThePredictionFollowsTheStatedFormula(unittest.TestCase):
    """CTDIvol = CTDIw x (mAs_rot / 100) / pitch, row by row."""

    def test_mas_per_rotation_is_current_times_time(self):
        for case in CASES:
            with self.subTest(case=case["file"], event=case["event_index"]):
                self.assertAlmostEqual(
                    case["mas_per_rotation"],
                    case["mean_tube_current_ma"] * case["exposure_time_per_rotation_s"],
                    places=9,
                )

    def test_the_estimate_is_the_formula_applied_to_the_table_value(self):
        for case in CASES:
            with self.subTest(case=case["file"], event=case["event_index"]):
                expected = (
                    case["table_ctdiw_mgy_per_100mas"]
                    * (case["mas_per_rotation"] / 100.0)
                    / case["pitch"]
                )
                self.assertAlmostEqual(case["estimated_ctdivol_mgy"], expected, places=6)

    def test_the_error_is_measured_against_the_recorded_value(self):
        """The reference belongs in the denominator, and the inverse is a different number.

        The two conventions coincide wherever the estimate equals the reference, and one
        event here is exact, so the guard that they are distinguishable at all is asked
        of the set rather than of every row.
        """
        separations = []
        for case in CASES:
            with self.subTest(case=case["file"], event=case["event_index"]):
                recorded = case["recorded_ctdivol_mgy"]
                estimated = case["estimated_ctdivol_mgy"]
                self.assertAlmostEqual(
                    case["percent_error"],
                    100.0 * (estimated - recorded) / recorded,
                    places=9,
                )
                inverted = 100.0 * (recorded - estimated) / estimated
                separations.append(abs(case["percent_error"] - inverted))
        self.assertGreater(
            max(separations),
            0.1,
            "no event distinguishes the two conventions, so this test proves nothing",
        )

    def test_the_implied_coefficient_inverts_the_formula(self):
        implied = []
        for case in CASES:
            value = (
                case["recorded_ctdivol_mgy"] * case["pitch"]
                / (case["mas_per_rotation"] / 100.0)
            )
            self.assertAlmostEqual(
                case["implied_nctdi_w_mgy_per_100mas"], value, places=6
            )
            implied.append(value)
        # Split by phantom: a median or standard deviation over both mixes two
        # quantities that differ by about a factor of two by construction.
        by_phantom = SUMMARY["implied_coefficient_mgy_per_100mas_by_phantom"]
        phantoms = sorted({c["phantom_cm"] for c in CASES})
        self.assertEqual(sorted(by_phantom), [f"{p}cm" for p in phantoms])
        for phantom in phantoms:
            rows = [
                c["implied_nctdi_w_mgy_per_100mas"]
                for c in CASES
                if c["phantom_cm"] == phantom
            ]
            block = by_phantom[f"{phantom}cm"]
            with self.subTest(phantom=phantom):
                self.assertEqual(block["n"], len(rows))
                self.assertAlmostEqual(block["min"], min(rows), places=6)
                self.assertAlmostEqual(block["max"], max(rows), places=6)
                self.assertAlmostEqual(
                    block["median"], statistics.median(rows), places=6
                )
                self.assertAlmostEqual(
                    block["mean"], statistics.fmean(rows), places=6
                )
                if len(rows) > 1:
                    self.assertAlmostEqual(
                        block["stdev"], statistics.stdev(rows), places=6
                    )
        self.assertNotIn(
            "implied_coefficient_mgy_per_100mas",
            SUMMARY,
            "the pooled coefficient summary is back; it mixes two phantoms",
        )

        # The narrowness claim is made for the body phantom only, where all four refuted
        # events sit; the 16 cm event has a different coefficient by construction, and an
        # earlier version of this assertion swept both into one band.
        # Per model as well as per phantom. Pooling the two models is the same mixing
        # objection this file raises against pooling the phantoms -- the Optima implies
        # 8.66-8.74 and the VCT 8.48-8.50 -- and a reviewer pointed that out. The pooled
        # 32 cm band is still checked, because that is the figure the manuscript quotes.
        body = [
            c["implied_nctdi_w_mgy_per_100mas"] for c in CASES if c["phantom_cm"] == 32
        ]
        self.assertGreaterEqual(len(body), 2, "no band to speak of")
        self.assertLess(
            max(body) - min(body),
            0.5,
            "the 32 cm band is reported as narrow; if it is not, the claim that the "
            "residual lies in the table lookup rather than the formula does not follow",
        )
        for model in sorted({c["table_model"] for c in CASES}):
            rows = [
                c["implied_nctdi_w_mgy_per_100mas"]
                for c in CASES
                if c["phantom_cm"] == 32 and c["table_model"] == model
            ]
            if len(rows) < 2:
                continue
            with self.subTest(model=model):
                self.assertLess(
                    max(rows) - min(rows),
                    0.5,
                    f"the within-model spread for {model} is what the narrowness claim "
                    f"rests on",
                )


class TheBeamWidthArgumentRestsOnAMeasuredTime(unittest.TestCase):
    """DCM 113824 is the event's total exposure time, and the scanner measured it.

    implied collimation = L x t_rot / (T x pitch), from four recorded numbers. Nothing
    here is an identity: T is a measurement and the three others describe the geometry
    the scanner claims, so the two can disagree -- and on four of five events they do.
    """

    def test_the_implied_width_is_recomputed_from_the_four_recorded_numbers(self):
        for case in CASES:
            check = case["beam_width_consistency"]
            if not check.get("measured"):
                continue
            with self.subTest(case=case["file"], event=case["event_index"]):
                implied = (
                    check["scanning_length_mm"]
                    * case["exposure_time_per_rotation_s"]
                    / (check["exposure_time_total_s"] * case["pitch"])
                )
                self.assertAlmostEqual(
                    check["implied_collimation_mm"], implied, places=6
                )
                self.assertGreater(
                    check["exposure_time_total_s"], 0, "a measured time must be positive"
                )

    def test_the_corroboration_flag_follows_the_stated_margin(self):
        for case in CASES:
            check = case["beam_width_consistency"]
            if not check.get("measured"):
                continue
            with self.subTest(case=case["file"], event=case["event_index"]):
                recorded = check["recorded_collimation_mm"]
                implied = check["implied_collimation_mm"]
                agrees = abs(recorded - implied) <= REFUTATION_MARGIN * implied
                self.assertEqual(check["corroborated"], agrees)
                self.assertAlmostEqual(
                    check["refutation_margin"], REFUTATION_MARGIN, places=9
                )

    def test_nothing_publishes_how_precisely_the_width_is_known(self):
        """A precision figure here would be an assumption, and would pick the table row.

        Any reading of how well ``w`` is known is dominated by the rotation time, which
        GE records as a discrete console setting rather than a rounded continuum. The arm
        published such a figure and used it to admit tabulated rows; two reviewers refused
        that on 2026-11-08. It must be absent, not hedged.
        """
        for case in CASES:
            check = case["beam_width_consistency"]
            band = case["reported_band"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                for withdrawn in (
                    "implied_collimation_relative_uncertainty",
                    "implied_collimation_half_width_mm",
                ):
                    self.assertNotIn(withdrawn, check)
                self.assertNotIn("measured_width_relative_uncertainty", band)
                self.assertIn("margin_note", check)
                self.assertIn("never to", check["margin_note"])

    def test_the_split_does_not_depend_on_where_the_margin_falls(self):
        """The two groups must be separated by far more than the margin.

        If a corroborated event sat just inside the margin and a refuted one just
        outside, the split would be an artefact of the number. It is not: the gap is
        0.6 % against 86-87 %, so every event clears the margin by at least a factor of
        three in whichever direction it falls, and any line between the two clusters
        gives the same answer. Measured here so that this is a result rather than an
        assertion.
        """
        inside, outside = [], []
        for case in CASES:
            check = case["beam_width_consistency"]
            if not check.get("measured"):
                continue
            relative = abs(
                check["recorded_collimation_mm"] - check["implied_collimation_mm"]
            ) / check["implied_collimation_mm"]
            (inside if check["corroborated"] else outside).append((case, relative))
        self.assertTrue(inside and outside, "the split needs both kinds of event")
        for case, relative in inside:
            with self.subTest(case=case["file"], side="corroborated"):
                self.assertLess(
                    relative,
                    REFUTATION_MARGIN / 3.0,
                    "a corroborated width sits close to the margin",
                )
        for case, relative in outside:
            with self.subTest(case=case["file"], side="refuted"):
                self.assertGreater(
                    relative,
                    REFUTATION_MARGIN * 3.0,
                    "a refuted width sits close to the margin",
                )

    def test_the_measured_time_contradicts_the_recorded_width_where_claimed(self):
        """The substantive check: measurement against prediction, not prediction twice.

        For a refuted event the duration the recorded collimation predicts must exceed
        the time the scanner measured by a wide margin. For the corroborated one the two
        must agree. Both sides are required, so neither can pass by accident.
        """
        case_by_key = {(c["file"], c["event_index"]): c for c in CASES}
        refuted, corroborated = [], []
        for case in CASES:
            check = case["beam_width_consistency"]
            if not check.get("measured"):
                continue
            ratio = (
                check["predicted_duration_at_recorded_collimation_s"]
                / check["exposure_time_total_s"]
            )
            (corroborated if check["corroborated"] else refuted).append(
                (case["file"], case["event_index"], ratio)
            )

        self.assertTrue(refuted, "no event supports the refutation")
        self.assertTrue(corroborated, "no event supports the corroboration")
        for name, index, ratio in refuted:
            with self.subTest(case=name, event=index, side="refuted"):
                # Refutation is two-sided: |recorded - implied| > margin * implied
                # admits a recorded width *larger* than the measured one, where the
                # ratio falls below 1 instead of rising above it. Requiring ratio > 4
                # for every refuted event would fail on such a case although the arm
                # handled it correctly -- a reviewer pointed that out on 2026-11-08.
                # What must hold either way is that the predicted duration is far from
                # the measured one.
                # Derived from the margin, not a fixed number. Refutation means
                # |recorded - implied| > m * implied, so recorded/implied lies outside
                # [1-m, 1+m] and this ratio, which equals implied/recorded, lies outside
                # [1/(1+m), 1/(1-m)]. A fixed threshold was asymmetric: it demanded
                # ratio < 0.25 on the side where the recorded width exceeds the measured
                # one, which the margin does not require. A reviewer found that twice.
                low = 1.0 / (1.0 + REFUTATION_MARGIN)
                high = 1.0 / (1.0 - REFUTATION_MARGIN)
                self.assertTrue(
                    ratio < low or ratio > high,
                    f"the recorded collimation predicts a scan {ratio:.2f} times the "
                    f"measured duration, inside [{low:.2f}, {high:.2f}], so this event "
                    f"should not have been refuted",
                )
        for name, index, ratio in corroborated:
            with self.subTest(case=name, event=index, side="corroborated"):
                # Dropped, deliberately: an assertion here could not fail. The ratio
                # equals implied/recorded algebraically, so corroboration already bounds
                # it to within the event's uncertainty, and asserting that again restates
                # test_the_corroboration_flag_follows_the_stated_margin. A reviewer
                # pointed this out on 2026-11-08. The substantive half of this test is
                # the refuted side above, where ratio > 4 is a fact about the reports and
                # not an identity.
                # No assertion on the ratio here. It equals implied/recorded
                # algebraically, so corroboration already bounds it and anything said
                # about it restates test_the_corroboration_flag_follows_the_stated_margin.
                # A reviewer pointed out that the previous "ratio > 0" could not fail.
                # What this side of the split is actually worth is checked by
                # test_the_predicted_duration_is_recomputed_from_its_own_inputs, which
                # rebuilds the stored duration from four recorded numbers.
                # Nothing is asserted on this side. The ratio equals implied/recorded
                # algebraically, so corroboration already bounds it, and the two
                # assertions tried here before -- a delta around 1.0 and then a
                # positivity check -- could neither of them fail. A reviewer said so
                # twice. The corroborated side's content is in
                # test_the_corroboration_flag_follows_the_stated_margin and in
                # test_the_predicted_duration_is_recomputed_from_its_own_inputs.
                del ratio

    def test_the_predicted_duration_is_recomputed_from_its_own_inputs(self):
        """duration at the recorded width = L / (recorded * pitch) * t_rot, row by row.

        This is the check that replaced the vacuous one above. It is not an identity
        about corroboration: it recomputes the stored figure from four recorded numbers,
        so a stored duration that does not follow from them fails here whatever the
        corroboration flag says.
        """
        for case in CASES:
            check = case["beam_width_consistency"]
            predicted = check.get("predicted_duration_at_recorded_collimation_s")
            recorded = check.get("recorded_collimation_mm")
            if predicted is None or not recorded:
                continue
            with self.subTest(case=case["file"], event=case["event_index"]):
                expected = (
                    check["scanning_length_mm"]
                    / (recorded * case["pitch"])
                    * case["exposure_time_per_rotation_s"]
                )
                self.assertAlmostEqual(predicted, expected, places=6)

    def test_the_implied_width_is_reported_and_never_substituted(self):
        """Nothing downstream may quietly adopt the width the clock implies.

        The previous form of this test asserted that ``table_collimation_mm`` differed
        from the unrounded implied width to three decimal places. Tabulated rows are
        10, 20 and 40 mm and the implied widths are ratios of four measured numbers, so
        that assertion held even if the code had snapped 19.04 mm to the 20 mm row --
        which is exactly the substitution it was meant to forbid. A reviewer found it on
        2026-11-08. The check is now that the row is a tabulated one and that it is the
        row the *recorded* width resolves to, which is what the point estimate is
        defined as; the implied width's influence is confined to
        ``reported_band.widths_mm``, where it is stated.
        """
        for case in CASES:
            check = case["beam_width_consistency"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                tabulated = case["assumption_envelope"]["collimations_tabulated"]
                self.assertIn(
                    case["table_collimation_mm"],
                    tabulated,
                    "the row used is not one the table holds",
                )
                nearest = min(
                    tabulated,
                    key=lambda c: (abs(c - case["recorded_collimation_mm"]), c),
                )
                self.assertAlmostEqual(
                    case["table_collimation_mm"],
                    nearest,
                    places=6,
                    msg="the point estimate's row is not the one nearest the RECORDED "
                    "width, so some other width has been substituted into it",
                )
                if not check.get("measured"):
                    continue
                self.assertAlmostEqual(
                    check["recorded_collimation_mm"],
                    case["recorded_collimation_mm"],
                    places=6,
                )


class TheReportedRangeSpansTheUndeterminedChoices(unittest.TestCase):
    """The arm's answer is an envelope, and an envelope that excludes nothing it should."""

    def test_the_envelope_holds_the_rows_the_published_table_holds(self):
        """The grid's size against an inventory written out here, not taken from itself.

        A reviewer pointed out on 2026-11-08 that checking the grid against the
        ``bowties_tabulated`` and ``collimations_tabulated`` lists in the same output
        cannot see a filter or width dropped from both. These counts come from the
        published coefficient table (Dinwiddie et al. 2026) for the three
        (model, phantom) combinations these events use, at 120 kVp.
        """
        EXPECTED = {
            ("Optima 660", 32): (["small", "large"], [10.0, 20.0, 40.0]),
            ("LightSpeed VCT", 32): (
                ["small", "medium", "large"],
                [10.0, 20.0, 40.0],
            ),
            ("LightSpeed VCT", 16): (["small", "medium"], [10.0, 20.0, 40.0]),
        }
        # What this cannot check, said rather than left implied: the coefficient *values*
        # in each cell. The published table is not in this snapshot, so a misextracted or
        # mislooked-up coefficient would pass every recomputation here, since every
        # recomputation uses the value the output carries. A reviewer named this on
        # 2026-11-08. The project's own suite holds the table and checks values against
        # it; this file checks that the grid's keys are the rows the table holds.
        seen = set()
        for case in CASES:
            key = (case["table_model"], case["phantom_cm"])
            self.assertIn(key, EXPECTED, f"no inventory recorded for {key}")
            bowties, widths = EXPECTED[key]
            envelope = case["assumption_envelope"]
            with self.subTest(model=key[0], phantom=key[1]):
                self.assertEqual(envelope["bowties_tabulated"], bowties)
                self.assertEqual(envelope["collimations_tabulated"], widths)
                self.assertEqual(
                    envelope["n_combinations"], len(bowties) * len(widths)
                )
                self.assertEqual(len(envelope["grid"]), len(bowties) * len(widths))
                self.assertEqual(
                    {(g["bowtie"], g["collimation_mm"]) for g in envelope["grid"]},
                    {(b, w) for b in bowties for w in widths},
                )
            seen.add(key)
        self.assertEqual(seen, set(EXPECTED), "an inventory entry went unused")

    def test_the_envelope_covers_every_tabulated_combination(self):
        for case in CASES:
            envelope = case["assumption_envelope"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                bowties = envelope["bowties_tabulated"]
                widths = envelope["collimations_tabulated"]
                self.assertEqual(
                    envelope["n_combinations"], len(bowties) * len(widths)
                )
                grid = envelope["grid"]
                self.assertEqual(len(grid), envelope["n_combinations"])
                self.assertEqual(
                    {(g["bowtie"], g["collimation_mm"]) for g in grid},
                    {(b, w) for b in bowties for w in widths},
                    "the grid is not the product of the tabulated choices",
                )
                errors = [g["percent_error"] for g in grid]
                self.assertAlmostEqual(
                    envelope["percent_error_min"], min(errors), places=9
                )
                self.assertAlmostEqual(
                    envelope["percent_error_max"], max(errors), places=9
                )

    def test_each_grid_point_follows_the_formula_from_its_own_coefficient(self):
        for case in CASES:
            for point in case["assumption_envelope"]["grid"]:
                with self.subTest(
                    case=case["file"], bowtie=point["bowtie"], mm=point["collimation_mm"]
                ):
                    expected = (
                        point["nctdi_w_mgy_per_100mas"]
                        * (case["mas_per_rotation"] / 100.0)
                        / case["pitch"]
                    )
                    self.assertAlmostEqual(
                        point["estimated_ctdivol_mgy"], expected, places=6
                    )
                    recorded = case["recorded_ctdivol_mgy"]
                    self.assertAlmostEqual(
                        point["percent_error"],
                        100.0 * (expected - recorded) / recorded,
                        places=9,
                    )

    def test_the_point_estimate_lies_inside_the_envelope_it_is_drawn_from(self):
        """If the published number sat outside its own envelope, one of them is wrong."""
        for case in CASES:
            envelope = case["assumption_envelope"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                self.assertGreaterEqual(
                    case["percent_error"], envelope["percent_error_min"] - 1e-9
                )
                self.assertLessEqual(
                    case["percent_error"], envelope["percent_error_max"] + 1e-9
                )

    def test_the_reported_band_follows_one_rule_for_every_event(self):
        """Recorded width stands, or every tabulated row stands plus both readings.

        The arm no longer selects a tabulated row from the measurement, because every
        rule that did rested on a chosen number. Where the measurement refutes the
        recorded width it publishes two spans: the wide one, assuming the width wholly
        undetermined, and the narrow one that follows from applying the same margin to
        the candidate rows. Neither is privileged. The wide one is what the band reports,
        because it is the weaker claim about the result -- not because it assumes less
        about the measurement, which it does not: it treats that measurement as decisive
        against the recorded width and indecisive against every other candidate, which is
        a position too. This docstring said "because it assumes least" for one round,
        contradicting the tool's own comment, and a reviewer found the contradiction.
        """
        for case in CASES:
            band = case["reported_band"]
            check = case["beam_width_consistency"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                tabulated = sorted(
                    {g["collimation_mm"] for g in case["assumption_envelope"]["grid"]}
                )
                if not check.get("measured"):
                    self.assertEqual(band["widths_mm"], tabulated)
                    self.assertEqual(
                        band["how_the_width_was_resolved"], "no measured exposure time"
                    )
                    self.assertNotIn("also_contradicted_removed", band)
                else:
                    recorded = check["recorded_collimation_mm"]
                    implied = check["implied_collimation_mm"]
                    relative = abs(recorded - implied) / implied
                    self.assertAlmostEqual(
                        band["recorded_vs_measured_relative_difference"],
                        relative,
                        places=9,
                    )
                    self.assertAlmostEqual(
                        band["refutation_margin_used"], REFUTATION_MARGIN, places=9
                    )
                    if relative <= REFUTATION_MARGIN:
                        self.assertEqual(
                            band["how_the_width_was_resolved"], "recorded width stands"
                        )
                        self.assertTrue(band["recorded_collimation_is_admitted"])
                        self.assertNotIn("also_contradicted_removed", band)
                        # Where the table holds the recorded width, that row alone and
                        # the filter is the only assumption. Where it does not, the
                        # coefficient is still a choice: the rows either side are both
                        # candidates, and substituting the nearest one -- which this
                        # oracle required for one round -- would hide that.
                        exact_rows = [
                            c for c in tabulated if abs(c - recorded) < 1e-9
                        ]
                        if exact_rows:
                            self.assertEqual(band["widths_mm"], exact_rows)
                            self.assertEqual(band["assumed"], "bowtie only")
                            self.assertTrue(band["recorded_collimation_is_tabulated"])
                        else:
                            # Every tabulated row, as for a refuted width: the recorded
                            # one stands but the table does not hold it, so the
                            # coefficient is a choice the report does not settle. This
                            # oracle required the nearest row, then the bracketing pair,
                            # and a reviewer refused each in turn -- the second because
                            # restricting to neighbours is no better evidenced than any
                            # other restriction.
                            self.assertEqual(band["widths_mm"], tabulated)
                            self.assertEqual(
                                band["assumed"], "bowtie and collimation"
                            )
                            self.assertFalse(
                                band["recorded_collimation_is_tabulated"]
                            )
                    else:
                        self.assertEqual(
                            band["widths_mm"],
                            tabulated,
                            "the reported span must assume the width undetermined",
                        )
                        self.assertEqual(
                            band["how_the_width_was_resolved"],
                            "undetermined within the table, recorded width refuted",
                        )
                        self.assertFalse(band["recorded_collimation_is_admitted"])
                        # And the consistent reading must be published beside it, with
                        # the same margin applied to the candidates.
                        narrowed = band["also_contradicted_removed"]
                        # No "or tabulated" here. That fallback was the defect, and
                        # an oracle carrying it would reject the corrected output and
                        # accept the broken one. Both reviewers named it.
                        expected = [
                            c for c in tabulated
                            if abs(c - implied) <= REFUTATION_MARGIN * implied
                        ]
                        self.assertEqual(narrowed["widths_mm"], expected)
                        if expected:
                            cells = [
                                g["percent_error"]
                                for g in case["assumption_envelope"]["grid"]
                                if g["collimation_mm"] in expected
                            ]
                            self.assertAlmostEqual(
                                narrowed["percent_error_min"], min(cells), places=9
                            )
                            self.assertAlmostEqual(
                                narrowed["percent_error_max"], max(cells), places=9
                            )
                            self.assertIn("margin is", narrowed["note"])
                        else:
                            self.assertEqual(narrowed["n"], 0)
                            self.assertIn("note_no_row_survives", narrowed)

                cells = [
                    g for g in case["assumption_envelope"]["grid"]
                    if g["collimation_mm"] in band["widths_mm"]
                ]
                errors = [g["percent_error"] for g in cells]
                self.assertEqual(band["n"], len(errors))
                self.assertAlmostEqual(band["percent_error_min"], min(errors), places=9)
                self.assertAlmostEqual(band["percent_error_max"], max(errors), places=9)

    def test_a_refuted_width_buys_no_narrowing_at_all(self):
        """Where the width is undetermined, the reported span is the whole envelope.

        Said as a property rather than a recomputation: if the reported band for a refuted
        event were ever narrower than the span over every tabulated combination, some rule
        would be selecting a row after all.
        """
        for case in CASES:
            band = case["reported_band"]
            if band.get("how_the_width_was_resolved") != (
                "undetermined within the table, recorded width refuted"
            ):
                continue
            envelope = case["assumption_envelope"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                self.assertAlmostEqual(
                    band["percent_error_min"], envelope["percent_error_min"], places=9
                )
                self.assertAlmostEqual(
                    band["percent_error_max"], envelope["percent_error_max"], places=9
                )

    def test_no_span_claims_to_bound_the_coefficient_at_an_untabulated_width(self):
        """Two descending endpoints say nothing about the function between them.

        An earlier version of the arm called the bracket a bound, checking only that the
        tabulated endpoints descend. Both reviewers refused that on 2026-11-08: the table
        gives three widths and nothing constrains nCTDIw in between, so the span is the
        range the choice allows. The claim must be absent from the output, not merely
        hedged.
        """
        for case in CASES:
            band = case["reported_band"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                for key in (
                    "span_is_a_bound_on_the_untabulated_width",
                    "coefficient_is_monotone_across_the_bracket",
                    "widths_outside_the_bracket_mm",
                ):
                    self.assertNotIn(key, band)
                self.assertNotIn("BOUNDS", band.get("because", ""))
                self.assertNotIn("bracket", band.get("because", ""))

    def test_a_corroborated_geometry_reports_the_filter_alone(self):
        """What the arm claims for a corroborated event must be what it publishes.

        The claim is that where the measurement confirms the recorded width, the width
        stops being an assumption. A reviewer found on 2026-11-08 that no test required
        it, while an exact-bracket rule was quietly breaking it. This is that test.
        """
        corroborated = [c for c in CASES if _corroborated(c)]
        self.assertTrue(corroborated, "no corroborated event to check")
        for case in corroborated:
            band = case["reported_band"]
            check = case["beam_width_consistency"]
            with self.subTest(case=case["file"], event=case["event_index"]):
                tabulated = sorted(
                    {g["collimation_mm"] for g in case["assumption_envelope"]["grid"]}
                )
                recorded = check["recorded_collimation_mm"]
                exact_rows = [c for c in tabulated if abs(c - recorded) < 1e-9]
                # Two oracles in this file disagreed here for one round -- this one
                # required the nearest row, the band-rule one required the bracketing
                # pair -- and a reviewer found it. The claim is about the filter only
                # where the table holds the recorded width.
                if exact_rows:
                    self.assertEqual(band["assumed"], "bowtie only")
                    self.assertEqual(band["widths_mm"], exact_rows)
                else:
                    # No restriction to neighbours here either; see the band-rule test.
                    self.assertEqual(band["assumed"], "bowtie and collimation")
                    self.assertEqual(band["widths_mm"], tabulated)
                self.assertTrue(band["recorded_collimation_is_admitted"])
                # It must really narrow something where the recorded width is
                # tabulated -- that is the claim. Where it is not, the code keeps every
                # row by design and there is nothing to narrow, so requiring narrowing
                # there contradicted the implementation; a reviewer caught that.
                if exact_rows:
                    self.assertLess(
                        band["percent_error_max"],
                        case["assumption_envelope"]["percent_error_max"] - 1e-9,
                    )
                else:
                    self.assertAlmostEqual(
                        band["percent_error_max"],
                        case["assumption_envelope"]["percent_error_max"],
                        places=9,
                    )

    def test_the_headline_is_the_union_of_the_bands_it_summarises(self):
        split = {
            "geometry_corroborated_by_exposure_time": [
                c for c in CASES if _corroborated(c)
            ],
            # Measured AND not corroborated, which is what _headline does. The test
            # filed every non-corroborated case as refuted, and the two agreed only
            # because n_without_a_measured_exposure_time is 0 on this data -- an oracle
            # that would diverge the moment a report arrived without DCM 113824. A
            # reviewer found it on 2026-11-08.
            "geometry_refuted_by_exposure_time": [
                c for c in CASES
                if c["beam_width_consistency"].get("measured") and not _corroborated(c)
            ],
        }
        self.assertEqual(
            HEADLINE["n_corroborated"], len(split["geometry_corroborated_by_exposure_time"])
        )
        self.assertEqual(
            HEADLINE["n_refuted"], len(split["geometry_refuted_by_exposure_time"])
        )
        self.assertEqual(
            HEADLINE["n_without_a_measured_exposure_time"],
            sum(
                1 for c in CASES if not c["beam_width_consistency"].get("measured")
            ),
        )
        self.assertEqual(
            HEADLINE["n_corroborated"]
            + HEADLINE["n_refuted"]
            + HEADLINE["n_without_a_measured_exposure_time"],
            len(CASES),
            "every case must fall in exactly one headline group",
        )
        for key, cases in split.items():
            band = HEADLINE[key]
            with self.subTest(band=key):
                self.assertEqual(band["n"], len(cases))
                if not cases:
                    continue
                lows = [c["reported_band"]["percent_error_min"] for c in cases]
                highs = [c["reported_band"]["percent_error_max"] for c in cases]
                self.assertAlmostEqual(
                    band["worst_case_percent_error_min"], min(lows), places=9
                )
                self.assertAlmostEqual(
                    band["worst_case_percent_error_max"], max(highs), places=9
                )
                within = all(
                    abs(c["reported_band"]["percent_error_min"]) <= 20.0
                    and abs(c["reported_band"]["percent_error_max"]) <= 20.0
                    for c in cases
                )
                self.assertEqual(
                    band["every_event_within_20_percent_for_every_assumption"], within
                )

                # The widest span the table allows, with the collimation treated as
                # unknown and the measurement used for nothing. It must really be
                # wider, or the narrowing is not stated.
                wide = band["over_every_tabulated_collimation_and_filter"]
                wide_lows = [
                    c["assumption_envelope"]["percent_error_min"] for c in cases
                ]
                wide_highs = [
                    c["assumption_envelope"]["percent_error_max"] for c in cases
                ]
                self.assertAlmostEqual(
                    wide["worst_case_percent_error_min"], min(wide_lows), places=9
                )
                self.assertAlmostEqual(
                    wide["worst_case_percent_error_max"], max(wide_highs), places=9
                )
                # These accept equality on purpose and are not claiming the wide span
                # is strictly wider: for a refuted group the two coincide, because
                # refuting the recorded width leaves every tabulated row standing. A
                # reviewer read an earlier comment here as claiming strictness, which it
                # could not check. What is checked is containment.
                self.assertLessEqual(
                    wide["worst_case_percent_error_min"],
                    band["worst_case_percent_error_min"] + 1e-9,
                )
                self.assertGreaterEqual(
                    wide["worst_case_percent_error_max"],
                    band["worst_case_percent_error_max"] - 1e-9,
                )
                # And where the group is corroborated, the wide span must be strictly
                # wider, since the recorded width is held there and giving it up really
                # does add rows.
                if key == "geometry_corroborated_by_exposure_time":
                    self.assertGreater(
                        wide["worst_case_percent_error_max"],
                        band["worst_case_percent_error_max"] + 1e-9,
                    )

    def test_the_narrowed_reading_aggregate_is_recomputed_from_the_cases(self):
        """The headline's alternative reading, and its absence where there is none.

        A reviewer pointed out on 2026-11-08 that nothing re-derived this, so a worst
        case computed over the wide band instead would have passed.
        """
        groups = {
            "geometry_corroborated_by_exposure_time": [
                c for c in CASES if _corroborated(c)
            ],
            "geometry_refuted_by_exposure_time": [
                c for c in CASES
                if c["beam_width_consistency"].get("measured") and not _corroborated(c)
            ],
        }
        for key, cases in groups.items():
            band = HEADLINE.get(key) or {}
            if not band.get("n"):
                continue
            narrowed = band.get("if_the_margin_is_applied_to_the_candidates_too")
            withs = [
                c["reported_band"]["also_contradicted_removed"]
                for c in cases
                if c["reported_band"].get("also_contradicted_removed", {}).get("n")
            ]
            with self.subTest(band=key):
                if not withs:
                    self.assertIsNone(
                        narrowed,
                        "no case offers the narrowed reading, so the aggregate must be "
                        "null rather than a figure from somewhere else",
                    )
                    continue
                self.assertIsNotNone(narrowed)
                self.assertEqual(narrowed["n"], len(withs))
                self.assertAlmostEqual(
                    narrowed["worst_case_percent_error_min"],
                    min(w["percent_error_min"] for w in withs),
                    places=9,
                )
                self.assertAlmostEqual(
                    narrowed["worst_case_percent_error_max"],
                    max(w["percent_error_max"] for w in withs),
                    places=9,
                )
                # It must really be narrower than the span it sits beside, or it is not
                # an alternative reading at all.
                self.assertGreaterEqual(
                    narrowed["worst_case_percent_error_min"],
                    band["worst_case_percent_error_min"] - 1e-9,
                )
                self.assertLessEqual(
                    narrowed["worst_case_percent_error_max"],
                    band["worst_case_percent_error_max"] + 1e-9,
                )

    def test_an_empty_candidate_set_is_said_rather_than_reversed(self):
        """Where no tabulated row survives the margin, that reading offers no span.

        The code restored every row in that case, which reverses the rule instead of
        applying it, and the oracle here copied the fallback and so endorsed it. Both
        reviewers found that on 2026-11-08. The five frozen events never reach the
        branch, so this checks the property the output must have rather than exercising
        it: a narrowed reading either carries rows and a span, or says no row survives.
        """
        for case in CASES:
            narrowed = case["reported_band"].get("also_contradicted_removed")
            if narrowed is None:
                continue
            tabulated = sorted(
                {g["collimation_mm"] for g in case["assumption_envelope"]["grid"]}
            )
            with self.subTest(case=case["file"], event=case["event_index"]):
                if narrowed["n"]:
                    self.assertTrue(narrowed["widths_mm"])
                    self.assertNotEqual(
                        narrowed["widths_mm"],
                        tabulated,
                        "a narrowed reading equal to every tabulated row is the wide "
                        "reading wearing the narrow one's label",
                    )
                    self.assertIn("percent_error_min", narrowed)
                else:
                    self.assertEqual(narrowed["widths_mm"], [])
                    self.assertNotIn("percent_error_min", narrowed)
                    self.assertIn("note_no_row_survives", narrowed)

    def test_the_withdrawn_selection_function_is_gone(self):
        """The output must not send a reader to a rule the code does not apply.

        ``widths_admitted_by_exposure_time`` held four successive selection rules and was
        left in place, uncalled, while the output still named it as the rule limiting the
        margin. A reviewer found that on 2026-11-08.
        """
        blob = json.dumps(PAYLOAD, ensure_ascii=False)
        self.assertNotIn("widths_admitted_by_exposure_time", blob)

    def test_no_point_estimate_is_published_without_saying_what_it_rests_on(self):
        """Every per-event accuracy summary must name its basis, or carry the band.

        The arm published ``summary.by_model``, an unlabelled describe() of the point
        errors with ``within_20_percent: 1.0``, beside the band that replaced it. For
        the Optima 660 those errors came from the tuple-order head filter at the 10 mm
        row, which that event's own exposure time refutes -- the very defect the band
        exists to remove, still printed next to it. A reviewer found it on 2026-11-08.

        So: any key summarising percent errors must either be a band, or say in its own
        name that it is the point estimate under the default assumptions.
        """
        suspicious = []
        for key, value in SUMMARY.items():
            if not isinstance(value, dict):
                continue
            entries = (
                value.values()
                if value and all(isinstance(v, dict) for v in value.values())
                else [value]
            )
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if "within_20_percent" not in entry:
                    continue
                if "point_estimate_under_the_default_assumptions" in key:
                    continue
                suspicious.append(key)
                break
        self.assertEqual(
            suspicious,
            [],
            f"these keys publish a within-20% figure without saying it is the point "
            f"estimate under the default assumptions: {suspicious}",
        )

    def test_the_per_model_summaries_are_recomputed_from_the_rows(self):
        """Both per-model blocks, value by value, against the cases of that model.

        A reviewer pointed out on 2026-11-08 that nothing checked these, so a per-model
        conclusion contradicting the bands could have been published beside them -- which
        is how the withdrawn single-number headline survived its first removal.
        """
        bands = SUMMARY["by_model_reported_band"]
        points = SUMMARY["by_model_point_estimate_under_the_default_assumptions"]
        models = sorted({c["table_model"] for c in CASES})
        self.assertEqual(sorted(bands), models)
        self.assertEqual(sorted(points), models)
        for model in models:
            rows = [c for c in CASES if c["table_model"] == model]
            with self.subTest(model=model):
                band = bands[model]
                self.assertEqual(band["n"], len(rows))
                lows = [c["reported_band"]["percent_error_min"] for c in rows]
                highs = [c["reported_band"]["percent_error_max"] for c in rows]
                self.assertAlmostEqual(
                    band["worst_case_percent_error_min"], min(lows), places=9
                )
                self.assertAlmostEqual(
                    band["worst_case_percent_error_max"], max(highs), places=9
                )
                self.assertEqual(
                    band["every_event_within_20_percent_for_every_assumption"],
                    all(abs(v) <= 20.0 for v in lows + highs),
                )

                point = points[model]
                errors = [c["percent_error"] for c in rows]
                self.assertEqual(point["n"], len(errors))
                self.assertAlmostEqual(point["min"], min(errors), places=9)
                self.assertAlmostEqual(point["max"], max(errors), places=9)
                self.assertAlmostEqual(
                    point["median"], statistics.median(errors), places=9
                )
                # Means and standard deviations too: a reviewer noted twice that the
                # file's claim to re-derive every reported number ran ahead of what it
                # actually checked.
                self.assertAlmostEqual(
                    point["mean"], statistics.fmean(errors), places=9
                )
                self.assertAlmostEqual(
                    point["mean_absolute"],
                    statistics.fmean(abs(e) for e in errors),
                    places=9,
                )
                if len(errors) > 1:
                    self.assertAlmostEqual(
                        point["stdev"], statistics.stdev(errors), places=9
                    )
                else:
                    self.assertNotIn("stdev", point)
                # And the point estimate must never be narrower than the band it sits
                # beside, or it is claiming more than the band allows.
                self.assertGreaterEqual(
                    band["worst_case_percent_error_max"] + 1e-9, point["max"]
                )
                self.assertLessEqual(
                    band["worst_case_percent_error_min"] - 1e-9, point["min"]
                )

    def test_a_quantity_is_not_summarised_as_if_it_were_a_percent_error(self):
        """within_20_percent on mGy per 100 mAs means only "below 20", and reads as more."""
        for phantom, implied in SUMMARY[
            "implied_coefficient_mgy_per_100mas_by_phantom"
        ].items():
            with self.subTest(phantom=phantom):
                self.assertIn("unit", implied, "a quantity's summary must carry its unit")
                self.assertIn("mGy", implied["unit"])
                for forbidden in (
                    "within_20_percent",
                    "within_50_percent",
                    "mean_absolute",
                ):
                    self.assertNotIn(forbidden, implied)

    def test_no_untabulated_filter_is_presented_as_a_choice_that_was_made(self):
        """Where the conventional filter is not measured, the band must span the rest.

        This is the defect that produced the previous headline: the conventional body
        filter is not tabulated for the Optima 660 at 32 cm, and a nearest-match rule
        broke the tie between the head and the body filter by the order of an internal
        list. A band over both is the honest answer; a single pick is not.
        """
        for case in CASES:
            envelope = case["assumption_envelope"]
            if envelope["conventional_bowtie_is_tabulated"]:
                continue
            with self.subTest(case=case["file"], event=case["event_index"]):
                self.assertGreaterEqual(
                    len(envelope["bowties_tabulated"]),
                    2,
                    "with no conventional filter measured there must be a range",
                )
                self.assertNotIn(
                    envelope["conventional_bowtie_for_phantom"],
                    envelope["bowties_tabulated"],
                    "the flag and the list disagree about what is tabulated",
                )
                self.assertLess(
                    envelope["percent_error_min"],
                    envelope["percent_error_max"],
                    "an undetermined filter cannot give a single error",
                )


class TheProvenanceSeparatesTheTwoClaims(unittest.TestCase):
    def test_the_file_says_what_the_arm_does_not_establish(self):
        provenance = PAYLOAD["provenance"]
        self.assertIn(
            "does NOT test the metadata-gap recovery path", provenance["what_this_tests"]
        )
        self.assertIn("not independent patients", provenance["independence"])
        self.assertGreaterEqual(len(provenance["eligibility_rules"]), 4)

    def test_the_published_rules_are_the_rules_that_were_applied(self):
        """A published rule that is not the rule applied misstates the comparison set.

        Until 2026-11-08 rule 3 still said "table feed (helical)" after the code had
        been corrected to admit sequenced acquisitions, and no rule mentioned the
        degenerate-geometry exclusion although four events are excluded by it. Both
        reviewers found it. The rules are therefore checked against the data they
        describe: every reason that excluded an event must be covered by a rule, and
        every acquisition type that was *admitted* must be one the rules allow.
        """
        rules = " ".join(PAYLOAD["provenance"]["eligibility_rules"]).lower()
        self.assertGreaterEqual(len(PAYLOAD["provenance"]["eligibility_rules"]), 5)

        # Each exclusion reason must be recognisable in the published rules.
        for reason in SUMMARY["exclusions_by_reason"]:
            with self.subTest(reason=reason[:48]):
                # Every reason exclusion_reason() can emit, not only the four the
                # frozen data happens to contain. A reviewer pointed out on 2026-11-08
                # that an unmapped reason would have asserted None is not None rather
                # than checking anything.
                key = (
                    "ctdivol" if "no recorded CTDIvol" in reason
                    else "degenerate" if "degenerate geometry" in reason
                    else "contradictory" if "contradictory geometry" in reason
                    else "stationary" if "table does not move" in reason
                    else "database" if "coefficient database" in reason
                    # "all recorded" rather than "incomplete": the rule names the
                    # fields that must be present, so the word "incomplete" is not in it.
                    # A reviewer pointed out that the key could never be found.
                    else "all recorded" if "incomplete technique" in reason
                    else "tabulated grid" if "outside the tabulated grid" in reason
                    else "models" if "not one the formula models" in reason
                    else None
                )
                self.assertIsNotNone(key, f"unmapped exclusion reason: {reason}")
                self.assertIn(key, rules, f"no published rule covers {reason!r}")

        # And each admitted acquisition type must be one the formula models, checked
        # against an independent list rather than against the rule text.
        #
        # Two earlier versions of this check could not fail. The first searched the rule
        # text for any word of the type's name, and every CID 10013 name ends in
        # "Acquisition" while the rules contain "acquisition". The second searched for the
        # discriminating word -- but the rules name stationary and free as *exclusions*,
        # so "stationary" is in the text either way and a wrongly admitted stationary
        # acquisition still passed. A reviewer found that on 2026-11-08. The admissible
        # set is therefore written out here, from CID 10013: the table moves in a spiral
        # and in a sequenced acquisition, and in no other type.
        ADMISSIBLE = {"spiral acquisition", "sequenced acquisition"}
        INADMISSIBLE = {
            "stationary acquisition",
            "constant angle acquisition",
            "free acquisition",
            "cone beam acquisition",
        }
        for case in CASES:
            kind = case["acquisition_type"].lower().strip()
            with self.subTest(type=kind):
                self.assertNotIn(
                    kind,
                    INADMISSIBLE,
                    f"a {case['acquisition_type']!r} was admitted; the table does not "
                    f"move in one, so mAs per rotation does not describe its exposure",
                )
                self.assertIn(
                    kind,
                    ADMISSIBLE,
                    f"{case['acquisition_type']!r} is not a type this formula models",
                )
                # And the published rules must say so.
                self.assertIn(kind.split()[0], rules)

    def test_the_output_says_the_reference_is_not_independent(self):
        """The exact agreement needs its explanation printed beside it.

        The one corroborated event's implied coefficient is 15.900000000000002 against a
        tabulated 15.9. That is not a measurement agreeing with a model: the reference is
        the CTDIvol the console displayed, pre-set by the manufacturer, and the open
        table's values are console figures normalized to 100 mAs. A reviewer asked why
        the agreement was exact; the answer belongs in the file.
        """
        key = "the_reference_and_the_coefficients_share_a_source"
        note = PAYLOAD["provenance"].get(key, "")
        self.assertTrue(note, f"provenance has no {key}")
        for phrase in ("console", "NOT independent", "in-factory"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, note)

    def test_the_headline_says_why_it_is_a_range(self):
        meaning = HEADLINE["what_it_means"]
        for phrase in ("bowtie", "collimation", "exposure time", "range"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, meaning)

    def test_the_formula_does_not_depend_on_what_an_rdsr_omits(self):
        """Slice thickness and scan range are supplied by the caller, so they must not move it."""
        check = PAYLOAD["formula_input_check"]
        self.assertTrue(check["invariant"])
        self.assertAlmostEqual(
            check["estimate_with_5mm_slice_100mm_range"],
            check["estimate_with_0.625mm_slice_700mm_range"],
            places=6,
        )

    def test_the_third_party_source_is_attributed_and_not_redistributed(self):
        source = PAYLOAD["provenance"]["sample_source"]
        self.assertIn("OpenREM", source)
        self.assertIn("GPL", source)
        self.assertIn("not", source.lower())


if __name__ == "__main__":
    unittest.main()
