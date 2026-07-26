"""Run the Layer-2 public-archive survey and write ``results/survey.{json,csv}``.

Measures, per manufacturer, how often a public CT series carries a recorded dose index
and -- where one exists -- how far the physics reconstruction is from it.

Usage::

    python tools/run_tcia_survey.py --screen-per-vendor 60 --fallback-per-vendor 8

Downloads are cached under ``--work-dir`` (default ``.tcia_work``), which is
git-ignored, so a re-run costs no bandwidth and the survey is reproducible from the
same seed.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from ctdose_core import __version__
from ctdose_core.survey import VENDOR_QUERIES, SurveyResult, TciaClient, run_survey

SCREEN_COLUMNS = [
    "vendor",
    "collection",
    "model_name",
    "model_resolution",
    "model_resolved",
    "n_images",
    "has_ctdivol_tag",
    "has_dlp_record",
    "has_tube_current",
    "has_timing",
    "has_collimation",
    "has_pitch",
    "has_slice_thickness",
    "has_slice_location",
    "fallback_feasible",
    "surveyable",
    "licence",
    "series_uid",
    "error",
]

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
    "series_uid",
]


def write_csvs(out: Path, result: SurveyResult) -> None:
    with (out / "survey.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCREEN_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for s in result.screens:
            w.writerow(s.to_dict())
    with (out / "survey_fallback.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FALLBACK_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for c in result.fallback_cases:
            w.writerow(c.to_dict())


def print_report(result: SurveyResult) -> None:
    payload = result.to_dict()
    absence = payload["tag_absence"]
    print("\n=== (a) recorded-dose absence in the image header ===")
    header = f"{'vendor':14s} {'n':>4s} {'CTDIvol':>9s} {'absent':>7s} {'fallback OK':>12s} {'surveyable':>11s}"
    print(header)
    print("-" * len(header))
    for vendor, block in payload["tag_absence"]["by_vendor"].items():
        n = block["n_series_readable"]
        print(
            f"{vendor:14s} {n:4d} {block['with_ctdivol_tag']:9d} "
            f"{(block['ctdivol_absence_rate'] or 0) * 100:6.1f}% "
            f"{(block['fallback_feasibility_rate'] or 0) * 100:11.1f}% "
            f"{(block['surveyable_rate'] or 0) * 100:10.1f}%"
        )
    o = absence["overall"]
    print(
        f"{'ALL':14s} {o['n_series_readable']:4d} {o['with_ctdivol_tag']:9d} "
        f"{(o['ctdivol_absence_rate'] or 0) * 100:6.1f}% "
        f"{(o['fallback_feasibility_rate'] or 0) * 100:11.1f}% "
        f"{(o['surveyable_rate'] or 0) * 100:10.1f}%"
    )

    unmatched = absence["unmatched_models"]
    if unmatched:
        print("\nscanner models not in the coefficient table (most frequent first):")
        for name, count in list(unmatched.items())[:12]:
            print(f"  {count:3d}  {name}")

    print("\n=== (b) physics fallback vs recorded CTDIvol ===")
    fb = payload["fallback_error"]
    if not fb["overall"]["n"]:
        print("  no series with a recorded CTDIvol could be evaluated")
    else:
        head = f"{'vendor':14s} {'n':>3s} {'median %':>9s} {'mean %':>8s} {'|mean| %':>9s} {'min':>8s} {'max':>8s}"
        print(head)
        print("-" * len(head))
        for vendor, d in fb["by_vendor"].items():
            if not d["n"]:
                continue
            print(
                f"{vendor:14s} {d['n']:3d} {d['median']:+9.1f} {d['mean']:+8.1f} "
                f"{d['mean_absolute']:9.1f} {d['min']:+8.1f} {d['max']:+8.1f}"
            )
        d = fb["overall"]
        print(
            f"{'ALL':14s} {d['n']:3d} {d['median']:+9.1f} {d['mean']:+8.1f} "
            f"{d['mean_absolute']:9.1f} {d['min']:+8.1f} {d['max']:+8.1f}"
        )

        d = fb["diagnostic_acquisitions_only"]
        if d["n"]:
            print(
                f"\n  diagnostic acquisitions only (recorded CTDIvol >= "
                f"{d['threshold_mgy']} mGy; {d['n_excluded']} excluded):"
            )
            print(
                f"    n={d['n']:3d}  median {d['median']:+7.1f}%  |mean| {d['mean_absolute']:6.1f}%"
                f"  range {d['min']:+.1f}..{d['max']:+.1f}%  "
                f"within +/-20%: {d['within_20_percent'] * 100:.0f}%  "
                f"within +/-50%: {d['within_50_percent'] * 100:.0f}%"
            )

        print("\n  stratified by whether total collimation was recorded in the header:")
        for label, key in (("recorded", "measured"), ("reconstructed", "reconstructed")):
            d = fb["by_collimation_provenance"][key]
            if not d["n"]:
                print(f"    {label:14s} n=0")
                continue
            print(
                f"    {label:14s} n={d['n']:3d}  median {d['median']:+7.1f}%  "
                f"|mean| {d['mean_absolute']:6.1f}%  range {d['min']:+.1f}..{d['max']:+.1f}%  "
                f"within +/-20%: {d['within_20_percent'] * 100:.0f}%"
            )
        print("    collimation provenance is vendor-dependent:")
        for vendor, counts in fb["vendor_by_collimation_provenance"].items():
            print(
                f"      {vendor:14s} recorded {counts['collimation_measured']:2d}, "
                f"reconstructed {counts['collimation_reconstructed']:2d}"
            )

        d = fb["diagnostic_and_collimation_measured"]
        if d["n"]:
            print(
                "\n  HEADLINE - diagnostic acquisitions whose header records collimation:"
            )
            print(
                f"    n={d['n']:3d}  median {d['median']:+7.1f}%  |mean| {d['mean_absolute']:6.1f}%"
                f"  IQR {d['p25']:+.1f}..{d['p75']:+.1f}%  range {d['min']:+.1f}..{d['max']:+.1f}%"
            )
            print(
                f"    within +/-20%: {d['within_20_percent'] * 100:.0f}%   "
                f"within +/-50%: {d['within_50_percent'] * 100:.0f}%   "
                f"({d['n_excluded']} of {fb['overall']['n']} cases excluded)"
            )
            for vendor, dv in d["by_vendor"].items():
                if dv["n"]:
                    print(
                        f"      {vendor:14s} n={dv['n']:2d}  median {dv['median']:+7.1f}%  "
                        f"|mean| {dv['mean_absolute']:6.1f}%"
                    )
        ind = fb["independence"]
        if ind["n_distinct_recorded_estimated_pairs"] < ind["n_cases"]:
            print(
                f"\n  note: {ind['n_cases']} cases but only "
                f"{ind['n_distinct_recorded_estimated_pairs']} distinct (recorded, estimated) "
                "pairs -- the archive re-releases some acquisitions, so the cases are "
                "not fully independent"
            )

        b = fb["best_case_if_model_variant_known"]
        if b["n"]:
            print(
                f"\n  optimistic bound if the scanner variant were identified: "
                f"median {b['median']:+.1f}%, |mean| {b['mean_absolute']:.1f}% (n={b['n']})"
            )
    print("\nexclusions from (b):")
    for reason, count in sorted(result.excluded.items()):
        print(f"  {count:4d}  {reason}")


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vendors", nargs="*", default=list(VENDOR_QUERIES))
    ap.add_argument("--screen-per-vendor", type=int, default=60)
    ap.add_argument("--fallback-per-vendor", type=int, default=8)
    ap.add_argument("--max-images", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20260726)
    ap.add_argument("--work-dir", type=Path, default=repo / ".tcia_work")
    ap.add_argument("--out", type=Path, default=repo / "results")
    args = ap.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    client = TciaClient(cache_dir=args.work_dir / "cache")
    started = datetime.now(UTC).isoformat(timespec="seconds")

    result = run_survey(
        vendors=args.vendors,
        screen_per_vendor=args.screen_per_vendor,
        fallback_per_vendor=args.fallback_per_vendor,
        max_images_for_download=args.max_images,
        seed=args.seed,
        client=client,
        work_dir=args.work_dir,
    )

    payload = result.to_dict()
    payload["provenance"] = {
        "layer": "2 - recorded-dose availability in a public archive, and fallback error",
        "generated_by": "tools/run_tcia_survey.py",
        "ctdose_core_version": __version__,
        "archive": "The Cancer Imaging Archive (TCIA), public NBIA REST API",
        "access_started_utc": started,
        "access_finished_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "survey.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_csvs(args.out, result)
    print_report(result)
    print(f"\nwrote {args.out / 'survey.json'}, survey.csv, survey_fallback.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
