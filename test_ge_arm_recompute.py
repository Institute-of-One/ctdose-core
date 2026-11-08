"""Re-derive every reported number of the GE validation arm from its own rows.

Standard ``unittest``, standard library only, and at the repository root on purpose.
The AI-Check framework runs ``python -I -m unittest discover -s .`` inside a snapshot of
the listed files: isolated mode ignores user site-packages, so numpy, scipy and pydicom
are not importable there, and discovery finds test modules at the snapshot root. The
project's real suite is pytest and stays where it is; this file is a second, independent
derivation that needs nothing but ``json`` and ``statistics``.

That independence is the point. ``tests/test_ge_rdsr_accuracy.py`` checks the *rules* by
driving the code that implements them, which cannot catch an error those rules share with
the code. This file never imports the project. It reads the committed result file and
recomputes the summary from ``cases``, so a summary that disagrees with the rows it
claims to summarise fails here even if every rule is implemented exactly as written.

What the arm reports, and what is re-derived below:

* four events, all within 20 % of the CTDIvol the scanner recorded;
* a median of +11.6 % and a range of +6.1 % to +16.4 %;
* an implied coefficient of 8.48 to 8.74 mGy per 100 mAs;
* the exclusion accounting, 35 events to 15 with a CTDIvol to 4 compared;
* the collimation sensitivity, reported but not adopted.
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


class TheSummaryAgreesWithItsRows(unittest.TestCase):
    """Recomputed from ``cases``, not read back from ``summary``."""

    def test_the_case_count_is_the_number_of_rows(self):
        self.assertEqual(SUMMARY["n_evaluated"], len(CASES))
        self.assertGreater(len(CASES), 0, "an arm with no rows reports nothing")

    def test_the_median_and_range_come_from_the_rows(self):
        errors = [c["percent_error"] for c in CASES]
        self.assertAlmostEqual(SUMMARY["overall"]["median"], statistics.median(errors), places=9)
        self.assertAlmostEqual(SUMMARY["overall"]["min"], min(errors), places=9)
        self.assertAlmostEqual(SUMMARY["overall"]["max"], max(errors), places=9)
        self.assertAlmostEqual(
            SUMMARY["overall"]["mean"], statistics.fmean(errors), places=9
        )

    def test_the_within_20_percent_fraction_is_recomputed(self):
        errors = [c["percent_error"] for c in CASES]
        within = sum(1 for e in errors if abs(e) <= 20.0) / len(errors)
        self.assertAlmostEqual(SUMMARY["overall"]["within_20_percent"], within, places=9)
        self.assertEqual(within, 1.0, "the arm reports that all four fall within 20 %")

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
        """The reference belongs in the denominator, and the inverse is a different number."""
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
                self.assertGreater(
                    abs(case["percent_error"] - inverted),
                    0.1,
                    "the two conventions must differ, or this test proves nothing",
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
        band = SUMMARY["implied_coefficient_mgy_per_100mas"]
        self.assertAlmostEqual(band["min"], min(implied), places=6)
        self.assertAlmostEqual(band["max"], max(implied), places=6)
        self.assertLess(
            max(implied) - min(implied),
            0.5,
            "the band is reported as narrow; if it is not, the claim that the residual "
            "lies in the table lookup rather than the formula does not follow",
        )


class TheBeamWidthArgumentIsArithmeticallySound(unittest.TestCase):
    """rotations = L / (collimation x pitch); duration = rotations x t_rot."""

    def test_the_scan_duration_is_recomputed_at_every_collimation(self):
        for case in CASES:
            check = case["beam_width_consistency"]
            length = check["scanning_length_mm"]
            if length <= 0:
                continue
            for width, entry in check["at_collimation_mm"].items():
                with self.subTest(case=case["file"], width=width):
                    rotations = length / (float(width) * case["pitch"])
                    self.assertAlmostEqual(entry["rotations"], rotations, places=6)
                    self.assertAlmostEqual(
                        entry["scan_duration_s"],
                        rotations * case["exposure_time_per_rotation_s"],
                        places=6,
                    )

    def test_the_recorded_width_is_flagged_as_the_recorded_one(self):
        for case in CASES:
            check = case["beam_width_consistency"]
            if not check["at_collimation_mm"]:
                continue
            flagged = [
                w for w, e in check["at_collimation_mm"].items()
                if e["is_the_recorded_value"]
            ]
            self.assertEqual(
                len(flagged), 1, f"{case['file']} flags {flagged} as recorded"
            )
            self.assertAlmostEqual(
                float(flagged[0]), case["recorded_collimation_mm"], places=6
            )

    def test_the_recorded_width_implies_an_implausible_duration(self):
        """The claim rests on this, so it is measured rather than asserted."""
        ratios = []
        for case in CASES:
            at = case["beam_width_consistency"]["at_collimation_mm"]
            if not at or "40" not in at:
                continue
            recorded = next(e for e in at.values() if e["is_the_recorded_value"])
            ratios.append(recorded["scan_duration_s"] / at["40"]["scan_duration_s"])
        self.assertTrue(ratios, "no case supports the comparison")
        self.assertGreater(
            min(ratios),
            4.0,
            "the recorded collimation must imply a markedly longer scan than a real "
            "beam width would, or the conclusion that it is not a beam width fails",
        )


class TheSensitivityIsReportedAndNotAdopted(unittest.TestCase):
    def test_the_headline_uses_the_collimation_the_report_records(self):
        for case in CASES:
            with self.subTest(case=case["file"], event=case["event_index"]):
                recorded = case["recorded_collimation_mm"]
                used = case["table_collimation_mm"]
                if recorded != used:
                    joined = " ".join(case["assumptions"])
                    self.assertIn(
                        f"{recorded:.1f} mm", joined,
                        "a substituted collimation must be recorded as an assumption",
                    )
                    self.assertIn(f"{used:.1f} mm", joined)

    def test_a_better_agreeing_row_exists_and_was_not_taken(self):
        """If the adopted row were already the best, the restraint would be untested."""
        sensitivity = SUMMARY["collimation_sensitivity"]["by_collimation_mm"]
        used = {c["table_collimation_mm"] for c in CASES}
        self.assertEqual(len(used), 1, "the cases do not share one table collimation")
        adopted = f"{int(used.pop())}mm"
        self.assertIn(adopted, sensitivity)
        best = min(sensitivity, key=lambda k: abs(sensitivity[k]["median"]))
        self.assertNotEqual(
            best, adopted,
            "the sensitivity analysis is only meaningful if a better-agreeing row was "
            "declined; here the adopted row is already the closest to zero",
        )
        self.assertIn("sensitivity only", SUMMARY["collimation_sensitivity"]["note"])


class TheProvenanceSeparatesTheTwoClaims(unittest.TestCase):
    def test_the_file_says_what_the_arm_does_not_establish(self):
        provenance = PAYLOAD["provenance"]
        self.assertIn(
            "does NOT test the metadata-gap recovery path", provenance["what_this_tests"]
        )
        self.assertIn("not independent patients", provenance["independence"])
        self.assertGreaterEqual(len(provenance["eligibility_rules"]), 4)

    def test_the_third_party_source_is_attributed_and_not_redistributed(self):
        source = PAYLOAD["provenance"]["sample_source"]
        self.assertIn("OpenREM", source)
        self.assertIn("GPL", source)
        self.assertIn("not", source.lower())


if __name__ == "__main__":
    unittest.main()
