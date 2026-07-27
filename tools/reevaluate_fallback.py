"""Recompute the fallback CTDIvol estimates of an existing survey, without re-sampling.

The Layer-2 survey has two parts. Part (a), tag absence, depends only on which attributes
a header carries, so a change to the *estimator* cannot alter it. Part (b), the fallback
error, is the estimator's output and must be recomputed whenever the estimator changes --
as it did when the tube-voltage correction and the bowtie inference were added.

Re-running ``run_tcia_survey.py`` would also re-sample the archive, and the archive is a
moving target: a new run screens a different set of series, so every number in the paper
would change for two reasons at once and neither could be attributed. This script instead
replays the estimator over *exactly the series the reported run downloaded*, which are
still in the local cache, and leaves the sample, the screens and the exclusion counts
untouched. The comparison is then clean: same series, same recorded values, new estimator.

Usage::

    python tools/reevaluate_fallback.py
    python tools/reevaluate_fallback.py --check     # report the deltas, write nothing

The deltas are always measured against whatever is currently in ``survey.json``, so a
second run reports zero changes: that is convergence, not a failure to apply the first
one. To see the effect of a change again, compare against the file in git.

Writes ``results/survey.json`` (fallback block and per-case rows) and
``results/survey_fallback.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from ctdose_core.io import load_series
from ctdose_core.survey import FallbackCase, SeriesScreen, evaluate_fallback, summarise_fallback

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"
SURVEY = RESULTS / "survey.json"

FALLBACK_COLUMNS = [
    "vendor",
    "collection",
    "model_name",
    "table_model",
    "region",
    "n_slices",
    "recorded_ctdivol_mgy",
    "estimated_ctdivol_mgy",
    "percent_error",
    "best_case_percent_error",
    "model_ambiguous",
    "collimation_measured",
    "collimation_mm",
    "pitch_measured",
    "kvp",
    "kv_reference",
    "kv_correction_factor",
    "estimated_uncorrected_mgy",
    "percent_error_uncorrected",
    "bowtie_inferred",
    "series_uid",
]

DERIVED_SCREEN_FIELDS = ("fallback_feasible", "surveyable")


def screen_for(payload: dict, series_uid: str) -> SeriesScreen:
    for row in payload["series"]:
        if row.get("series_uid") == series_uid:
            return SeriesScreen(
                **{k: v for k, v in row.items() if k not in DERIVED_SCREEN_FIELDS}
            )
    raise KeyError(series_uid)


def cached_dir(work: Path, series_uid: str) -> Path:
    """Where ``run_survey`` put this series' images."""
    return work / "series" / series_uid[-24:]


def recompute(payload: dict, work: Path) -> tuple[list[FallbackCase], list[dict[str, Any]]]:
    cases: list[FallbackCase] = []
    deltas: list[dict[str, Any]] = []
    for old in payload["fallback_cases"]:
        uid = old["series_uid"]
        directory = cached_dir(work, uid)
        if not directory.is_dir():
            raise SystemExit(
                f"no cached images for {uid} at {directory}.\n"
                "This script replays the reported run and cannot invent the images; "
                "re-run tools/run_tcia_survey.py to download them again (which re-samples)."
            )
        screen = screen_for(payload, uid)
        series = load_series(directory)
        case = evaluate_fallback(screen, series, region=old["region"])
        if case is None:
            raise SystemExit(
                f"{uid}: the estimator now declines a case the reported run evaluated. "
                "That is a substantive change, not a recomputation -- investigate before "
                "overwriting the results."
            )
        cases.append(case)
        deltas.append(
            {
                "vendor": case.vendor,
                "series_uid": uid,
                "model_name": case.model_name,
                "kvp": series.kvp,
                "recorded_ctdivol_mgy": case.recorded_ctdivol_mgy,
                "old_estimate_mgy": old["estimated_ctdivol_mgy"],
                "new_estimate_mgy": case.estimated_ctdivol_mgy,
                "old_percent_error": old["percent_error"],
                "new_percent_error": case.percent_error,
                "old_table_model": old["table_model"],
                "new_table_model": case.table_model,
                "was_ambiguous": old["model_ambiguous"],
                "is_ambiguous": case.model_ambiguous,
            }
        )
    return cases, deltas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", type=Path, default=SURVEY)
    ap.add_argument("--work-dir", type=Path, default=REPO / ".tcia_work")
    ap.add_argument("--check", action="store_true", help="report deltas without writing")
    args = ap.parse_args()

    payload = json.loads(args.survey.read_text(encoding="utf-8"))
    cases, deltas = recompute(payload, args.work_dir)

    changed = [d for d in deltas if abs(d["new_estimate_mgy"] - d["old_estimate_mgy"]) > 1e-9]
    disambiguated = [d for d in deltas if d["was_ambiguous"] and not d["is_ambiguous"]]

    header = f"{'vendor':14s} {'kVp':>4s} {'rec':>7s} {'old':>8s} {'new':>8s} {'old%':>8s} {'new%':>8s}"
    print(header)
    print("-" * len(header))
    for d in sorted(deltas, key=lambda x: (x["vendor"], x["series_uid"])):
        mark = " *" if abs(d["new_estimate_mgy"] - d["old_estimate_mgy"]) > 1e-9 else ""
        kvp = f"{d['kvp']:4.0f}" if d["kvp"] else "   ?"
        print(
            f"{d['vendor']:14s} {kvp} {d['recorded_ctdivol_mgy']:7.2f} "
            f"{d['old_estimate_mgy']:8.2f} {d['new_estimate_mgy']:8.2f} "
            f"{d['old_percent_error']:+8.1f} {d['new_percent_error']:+8.1f}{mark}"
        )
    print(
        f"\n{len(changed)} of {len(deltas)} estimates changed; "
        f"{len(disambiguated)} models disambiguated by the inferred bowtie"
    )
    old_summary = payload["fallback_error"]
    new_summary = summarise_fallback(cases)
    for block, label in ((old_summary, "before"), (new_summary, "after")):
        overall = block.get("overall", block)
        print(f"  {label}: {json.dumps(overall.get('absolute_percent_error', overall))[:160]}")

    if args.check:
        print("\n--check: nothing written")
        return 0

    payload["fallback_error"] = new_summary
    payload["fallback_cases"] = [c.to_dict() for c in cases]
    payload.setdefault("provenance", {})["fallback_recomputed_by"] = "tools/reevaluate_fallback.py"
    payload["provenance"]["fallback_recomputed_note"] = (
        "The sample, the screens and the exclusion counts are those of the archive run "
        "recorded in access_started_utc/access_finished_utc. Only the reconstructed "
        "CTDIvol of each case was recomputed, from the same cached images, after the "
        "tube-voltage correction and the bowtie inference were added to the estimator."
    )
    args.survey.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    csv_path = args.survey.parent / "survey_fallback.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FALLBACK_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for c in cases:
            w.writerow(c.to_dict())
    print(f"\nwrote {args.survey}\nwrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
