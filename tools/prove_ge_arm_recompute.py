"""Prove the rewritten standalone GE test by injection.

Rule 7 of D:\\DevGit\\AGENTS.md: a checker is worth nothing until a known defect has been
put in front of it and it refused. Each injection below is a defect the arm could really
have, and the one the previous version of this test could not see is injection 6: making
the measured exposure time agree with the duration the recorded collimation predicts.
The old assertion compared two predictions, so it was an identity and could not fail.

Run from anywhere:
    python tools/prove_ge_arm_recompute.py
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
TEST = REPO / "test_ge_arm_recompute.py"
RESULT = REPO / "results" / "ge_rdsr_accuracy.json"


def _refuted_index(payload: dict) -> int:
    for i, case in enumerate(payload["cases"]):
        if not case["beam_width_consistency"].get("corroborated"):
            return i
    raise AssertionError("no refuted case to injure")


def _corroborated_index(payload: dict) -> int:
    for i, case in enumerate(payload["cases"]):
        if case["beam_width_consistency"].get("corroborated"):
            return i
    raise AssertionError("no corroborated case to injure")


def claim_a_refuted_geometry_is_corroborated(p: dict) -> None:
    p["cases"][_refuted_index(p)]["beam_width_consistency"]["corroborated"] = True


def adopt_the_implied_width_as_the_table_row(p: dict) -> None:
    case = p["cases"][_refuted_index(p)]
    case["table_collimation_mm"] = case["beam_width_consistency"]["implied_collimation_mm"]


def narrow_a_refuted_band_to_one_number(p: dict) -> None:
    case = p["cases"][_refuted_index(p)]
    case["reported_band"]["percent_error_min"] = case["percent_error"]
    case["reported_band"]["percent_error_max"] = case["percent_error"]


def select_a_row_for_a_refuted_width(p: dict) -> None:
    """Pick the tabulated row nearest the measured width on a refuted event.

    This is the defect the arm spent four versions on: the measurement refutes the
    recorded width but cannot say which row the scan used, so narrowing to one is a
    choice presented as a measurement. Everything is kept internally consistent, which
    is how each earlier version passed its own tests.
    """
    case = p["cases"][_refuted_index(p)]
    band = case["reported_band"]
    implied = case["beam_width_consistency"]["implied_collimation_mm"]
    rows = sorted({g["collimation_mm"] for g in case["assumption_envelope"]["grid"]})
    nearest = min(rows, key=lambda c: abs(c - implied))
    cells = [
        g["percent_error"]
        for g in case["assumption_envelope"]["grid"]
        if g["collimation_mm"] == nearest
    ]
    band["widths_mm"] = [nearest]
    band["assumed"] = "bowtie only"
    band["n"] = len(cells)
    band["percent_error_min"], band["percent_error_max"] = min(cells), max(cells)
    group = p["summary"]["headline"]["geometry_refuted_by_exposure_time"]
    lows = [
        c["reported_band"]["percent_error_min"]
        for c in p["cases"]
        if not c["beam_width_consistency"].get("corroborated")
    ]
    highs = [
        c["reported_band"]["percent_error_max"]
        for c in p["cases"]
        if not c["beam_width_consistency"].get("corroborated")
    ]
    group["worst_case_percent_error_min"] = min(lows)
    group["worst_case_percent_error_max"] = max(highs)
    group["every_event_within_20_percent_for_every_assumption"] = all(
        abs(v) <= 20.0 for v in lows + highs
    )


def republish_a_precision_figure_for_the_measured_width(p: dict) -> None:
    """Put back a number saying how precisely the width is known.

    Any such figure is dominated by a rotation time recorded as a discrete console
    setting, so it is an assumption wearing a measurement's clothes -- and it is what
    four earlier versions used to pick a table row.
    """
    for case in p["cases"]:
        check = case["beam_width_consistency"]
        if not check.get("measured"):
            continue
        check["implied_collimation_relative_uncertainty"] = 0.10
        check["implied_collimation_half_width_mm"] = (
            0.10 * check["implied_collimation_mm"]
        )
        return
    raise AssertionError("no measured event to annotate")


def restore_the_old_helical_only_rule(p: dict) -> None:
    """Publish the rule that was not applied -- the second finding of that review."""
    p["provenance"]["eligibility_rules"] = [
        "the event records a CTDIvol (DCM 113830)",
        "the scanner model resolves in the open coefficient database, exactly or by an "
        "alias denoting the same machine",
        "the acquisition has table feed (helical), because the formula models one "
        "rotation's output spread along the feed",
        "tube potential, mean tube current, exposure time per rotation, pitch, total "
        "collimation and the CTDIw phantom code are all recorded",
    ]


def drop_the_shared_source_caveat(p: dict) -> None:
    """Remove the note that the reference and the coefficients are both console figures."""
    p["provenance"].pop("the_reference_and_the_coefficients_share_a_source", None)


def widen_the_tolerance_until_the_split_is_arbitrary(p: dict) -> None:
    """Move a refuted event's implied width so that it sits beside the tolerance."""
    case = p["cases"][_refuted_index(p)]
    check = case["beam_width_consistency"]
    check["implied_collimation_mm"] = check["recorded_collimation_mm"] / 0.88


def restore_every_row_when_no_candidate_survives(p: dict) -> None:
    """The boundary both reviewers found: an empty candidate set reversing the rule.

    Where no tabulated row is within the margin of the measured width, the narrowed
    reading has nothing to offer; restoring every row labels the wide reading as the
    narrow one. The five frozen events never reach this branch -- each has a row within
    the margin -- so the defect is injected by emptying one event's candidate set and
    filling it back with the whole table, exactly as the withdrawn fallback did.
    """
    for case in p["cases"]:
        narrowed = case["reported_band"].get("also_contradicted_removed")
        if not narrowed or not narrowed.get("n"):
            continue
        grid = case["assumption_envelope"]["grid"]
        rows = sorted({g["collimation_mm"] for g in grid})
        errors = [g["percent_error"] for g in grid]
        narrowed["widths_mm"] = rows
        narrowed["n"] = len(errors)
        narrowed["percent_error_min"] = min(errors)
        narrowed["percent_error_max"] = max(errors)
        group = p["summary"]["headline"]["geometry_refuted_by_exposure_time"]
        agg = group.get("if_the_margin_is_applied_to_the_candidates_too")
        if agg:
            withs = [
                c["reported_band"]["also_contradicted_removed"]
                for c in p["cases"]
                if c["reported_band"].get("also_contradicted_removed", {}).get("n")
            ]
            agg["n"] = len(withs)
            agg["worst_case_percent_error_min"] = min(
                w["percent_error_min"] for w in withs
            )
            agg["worst_case_percent_error_max"] = max(
                w["percent_error_max"] for w in withs
            )
        return
    raise AssertionError("no narrowed reading to empty")


def claim_the_arithmetic_was_reproduced_where_it_was_not(p: dict) -> None:
    """Shift one span clear of zero while still calling the arithmetic reproduced.

    The pass condition the arm gained on 2026-11-08 is that each span contains 0. An
    event whose span excluded it would be one the table cannot reproduce however the
    undetermined inputs are chosen, and the summary flag must follow.
    """
    case = p["cases"][0]
    band = case["reported_band"]
    shift = band["percent_error_min"] + 5.0
    band["percent_error_min"] -= shift
    band["percent_error_max"] -= shift
    # Left deliberately true, which is the defect.
    case["arithmetic_reproduced"] = True


def republish_the_unlabelled_per_model_point_estimate(p: dict) -> None:
    """The second review's finding: the withdrawn number, beside the band."""
    by_model = {}
    for case in p["cases"]:
        by_model.setdefault(case["table_model"], []).append(case["percent_error"])
    p["summary"]["by_model"] = {
        model: {
            "n": len(errors),
            "median": sorted(errors)[len(errors) // 2],
            "min": min(errors),
            "max": max(errors),
            "within_20_percent": float(all(abs(e) <= 20.0 for e in errors)),
        }
        for model, errors in by_model.items()
    }


def summarise_the_coefficients_as_percent_errors(p: dict) -> None:
    """within_20_percent on mGy per 100 mAs reads as an accuracy claim and is not one."""
    for block in p["summary"]["implied_coefficient_mgy_per_100mas_by_phantom"].values():
        block.pop("unit", None)
        block["within_20_percent"] = 1.0
        block["within_50_percent"] = 1.0
        block["mean_absolute"] = block["mean"]


def swap_two_exclusion_counts(p: dict) -> None:
    """Keep the total right and the reasons present, but attribute them wrongly.

    The two keys must carry **different** counts, or the swap changes nothing and the
    injection proves nothing. A first version of this function picked the first two keys
    that were not the largest, which on this data are both 4, so it was a no-op and
    looked like a gate failure.
    """
    reasons = p["summary"]["exclusions_by_reason"]
    pair = next(
        (
            (a, b)
            for i, a in enumerate(reasons)
            for b in list(reasons)[i + 1 :]
            if reasons[a] != reasons[b]
        ),
        None,
    )
    if pair is None:
        raise AssertionError("no two exclusion reasons carry different counts to swap")
    a, b = pair
    reasons[a], reasons[b] = reasons[b], reasons[a]


def name_an_admitted_type_as_a_reason_for_exclusion(p: dict) -> None:
    """Publish rules that exclude a type the run admitted."""
    kinds = {c["acquisition_type"].lower().replace("acquisition", "").strip() for c in p["cases"]}
    kind = sorted(kinds)[0]
    p["provenance"]["eligibility_rules"] = [
        r for r in p["provenance"]["eligibility_rules"] if kind not in r.lower()
    ]


def drop_a_filter_from_the_envelope(p: dict) -> None:
    """Report only the better-agreeing bowtie, which is the defect the review found."""
    case = p["cases"][_refuted_index(p)]
    env = case["assumption_envelope"]
    keep = env["bowties_tabulated"][0]
    env["grid"] = [g for g in env["grid"] if g["bowtie"] == keep]
    env["bowties_tabulated"] = [keep]
    env["n_combinations"] = len(env["grid"])
    errors = [g["percent_error"] for g in env["grid"]]
    env["percent_error_min"], env["percent_error_max"] = min(errors), max(errors)


def shrink_the_headline_to_look_better(p: dict) -> None:
    p["summary"]["headline"]["geometry_refuted_by_exposure_time"][
        "worst_case_percent_error_max"
    ] = 12.0


def make_the_measured_time_agree_with_the_recorded_width(p: dict) -> None:
    """The injection the old identity-based assertion could not catch.

    If the scanner's measured total exposure time matched the duration the recorded
    collimation predicts, that collimation would not be refuted -- so the arm's split,
    and its corroborated flag, must change. A test that compared the predicted duration
    at the recorded width against the predicted duration at 40 mm sees nothing here,
    because neither prediction moves.
    """
    case = p["cases"][_refuted_index(p)]
    check = case["beam_width_consistency"]
    check["exposure_time_total_s"] = check[
        "predicted_duration_at_recorded_collimation_s"
    ]


def rewrite_the_corroborated_event_as_inexact(p: dict) -> None:
    """A corroborated width must predict the measured time; break that."""
    case = p["cases"][_corroborated_index(p)]
    case["beam_width_consistency"]["predicted_duration_at_recorded_collimation_s"] *= 3.0


def lose_an_exclusion_reason(p: dict) -> None:
    """Collapse the three reasons into one, hiding which rule did the work."""
    reasons = p["summary"]["exclusions_by_reason"]
    total = sum(reasons.values())
    p["summary"]["exclusions_by_reason"] = {"excluded": total}


def put_the_point_estimate_outside_its_envelope(p: dict) -> None:
    case = p["cases"][_refuted_index(p)]
    case["percent_error"] = case["assumption_envelope"]["percent_error_max"] + 5.0


INJECTIONS = [
    ("a refuted geometry marked corroborated", claim_a_refuted_geometry_is_corroborated),
    ("the implied width adopted as the table row", adopt_the_implied_width_as_the_table_row),
    ("a refuted band narrowed to one number", narrow_a_refuted_band_to_one_number),
    ("the worse bowtie dropped from the envelope", drop_a_filter_from_the_envelope),
    ("the headline shrunk below its rows", shrink_the_headline_to_look_better),
    (
        "the measured time made to agree with the recorded width",
        make_the_measured_time_agree_with_the_recorded_width,
    ),
    ("the corroborated event made inexact", rewrite_the_corroborated_event_as_inexact),
    ("three exclusion reasons collapsed into one", lose_an_exclusion_reason),
    ("a point estimate outside its own envelope", put_the_point_estimate_outside_its_envelope),
    # Removed on 2026-11-08: this injection widened a refuted event's band to the full
    # envelope, which was a defect while the arm selected a row from the measurement and
    # is the correct behaviour now that it does not. Its inverse, selecting a row for a
    # refuted width, is the injection below.
    ("the published rules reverted to helical-only", restore_the_old_helical_only_rule),
    ("the shared-source caveat removed", drop_the_shared_source_caveat),
    (
        "a refuted event moved to sit beside the tolerance",
        widen_the_tolerance_until_the_split_is_arbitrary,
    ),
    ("a row selected for a refuted width", select_a_row_for_a_refuted_width),
    (
        "a span clear of zero still called reproduced",
        claim_the_arithmetic_was_reproduced_where_it_was_not,
    ),
    (
        "every row restored where no candidate survives the margin",
        restore_every_row_when_no_candidate_survives,
    ),
    (
        "a precision figure republished for the measured width",
        republish_a_precision_figure_for_the_measured_width,
    ),
    (
        "the unlabelled per-model point estimate republished",
        republish_the_unlabelled_per_model_point_estimate,
    ),
    (
        "coefficients summarised as if they were percent errors",
        summarise_the_coefficients_as_percent_errors,
    ),
    ("two exclusion counts swapped, total unchanged", swap_two_exclusion_counts),
    (
        "an admitted acquisition type dropped from the published rules",
        name_an_admitted_type_as_a_reason_for_exclusion,
    ),
]


def run(snapshot: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-m", "unittest", "discover", "-s", "."],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    clean = json.loads(RESULT.read_text(encoding="utf-8"))
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "results").mkdir()
        shutil.copy2(TEST, root / TEST.name)

        # The unaltered file must pass, or every refusal below is meaningless.
        (root / "results" / RESULT.name).write_text(
            json.dumps(clean, indent=2), encoding="utf-8"
        )
        baseline = run(root)
        if baseline.returncode != 0:
            print("BASELINE FAILED -- the test does not pass on the real result file")
            print(baseline.stderr[-3000:])
            return 1
        ran = [line for line in baseline.stderr.splitlines() if line.startswith("Ran ")]
        print(f"baseline: PASS ({ran[0] if ran else 'unknown count'})")

        for label, injure in INJECTIONS:
            payload = copy.deepcopy(clean)
            injure(payload)
            (root / "results" / RESULT.name).write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            proc = run(root)
            caught = proc.returncode != 0
            print(f"  {'CAUGHT ' if caught else 'MISSED '} {label}")
            if caught:
                named = [
                    line.split(" ", 1)[1].split(" ")[0]
                    for line in proc.stderr.splitlines()
                    if line.startswith(("FAIL: ", "ERROR: "))
                ]
                for n in dict.fromkeys(named):
                    print(f"            by {n}")
            else:
                failures.append(label)

        # Restore, and prove the restore is byte-identical to what we started from.
        (root / "results" / RESULT.name).write_text(
            json.dumps(clean, indent=2), encoding="utf-8"
        )
        if run(root).returncode != 0:
            print("the restored file no longer passes")
            return 1

    if failures:
        print(f"\n{len(failures)} injection(s) went undetected:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nall {len(INJECTIONS)} injections refused; the real result file passes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
