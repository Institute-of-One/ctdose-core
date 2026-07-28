"""Evaluate the open-table reconstruction against every recorded CTDIvol it can reach.

This is the accuracy arm rebuilt on the open coefficient database (Dinwiddie et al. 2026,
CC BY 4.0). It replaces the previous run, which used CT-Expo-derived coefficients, and it
selects its cases differently.

The previous run sampled from whatever the coefficient table happened to cover and capped
at 14 per manufacturer for download budget. The open database covers 17 named scanners, so
the eligible set is now *defined* rather than sampled: every screened series whose scanner
is in the database, that carries a recorded CTDIvol to compare against, and whose
acquisition parameters are complete. No cap and no seed, because there is nothing left to
choose -- which removes the "capped subsample" caveat from the results.

One vendor is absent from the comparison for a reason worth stating plainly. GE is the
best-covered vendor in the open database, but **no GE series in the archive carries a
recorded CTDIvol**, so there is nothing to validate GE against. GE is not excluded here;
the archive simply offers no reference for it.

Usage::

    python tools/evaluate_open_fallback.py
    python tools/evaluate_open_fallback.py --no-download   # cached series only

Writes ``results/open_fallback.json``.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from ctdose_core.ctdi_table import (
    CoefficientNotAvailable,
    covers,
    provenance,
    resolve_model,
)
from ctdose_core.io import load_series
from ctdose_core.metrics import (
    acquisition_from_series,
    estimate_ctdivol_open,
    fallback_error,
)
from ctdose_core.provenance import Source
from ctdose_core.ssde import phantom_for
from ctdose_core.survey import (
    DIAGNOSTIC_CTDIVOL_MGY,
    TciaClient,
    implausible_recorded_ctdivol,
    region_for,
)

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"


def eligible(survey: dict) -> list[dict]:
    """Screened series the open database can reconstruct AND that carry a reference."""
    out = []
    for s in survey["series"]:
        if s.get("error") or not s.get("has_ctdivol_tag") or not s.get("fallback_feasible"):
            continue
        if covers(s.get("manufacturer_raw", ""), s.get("model_name", "")):
            out.append(s)
    return out


def evaluate(screen: dict, series, region: str) -> dict[str, Any] | None:
    recorded = series.ctdivol_tag.value if series.ctdivol_tag else None
    if not recorded or implausible_recorded_ctdivol(float(recorded)):
        return None
    try:
        acq = acquisition_from_series(series)
    except ValueError:
        return None

    vendor, model = resolve_model(screen.get("manufacturer_raw", ""), screen["model_name"])
    phantom = phantom_for(region, None)
    est = estimate_ctdivol_open(acq, vendor=vendor, model=model, phantom_cm=int(phantom))

    err = fallback_error(float(recorded), float(est))
    assumptions = [n for n in est.notes if not n.startswith("reconstructed because")]
    return {
        "vendor": screen["vendor"],
        "collection": screen["collection"],
        "series_uid": screen["series_uid"],
        "model_name": screen["model_name"],
        "table_vendor": vendor,
        "table_model": model,
        "region": region,
        "phantom_cm": int(phantom),
        "n_slices": series.n_slices,
        "kvp": series.kvp,
        "collimation_mm": acq.collimation_mm,
        "collimation_measured": (
            series.collimation_mm is not None
            and series.collimation_mm.source is not Source.PHYSICS_FALLBACK
        ),
        "pitch": acq.pitch,
        "pitch_measured": (
            series.pitch is not None and series.pitch.source is not Source.PHYSICS_FALLBACK
        ),
        "mas_per_rotation": acq.mas_per_rotation,
        "recorded_ctdivol_mgy": float(recorded),
        "estimated_ctdivol_mgy": float(est),
        "relative_error": err["relative_error"],
        "percent_error": err["percent_error"],
        "table_ctdiw_mgy_per_100mas": est.inputs["CTDIw_mGy_per_100mAs"],
        "table_kvp": est.inputs["table_kvp"],
        "table_collimation_mm": est.inputs["table_collimation_mm"],
        "table_bowtie": est.inputs["table_bowtie"],
        "exact_table_match": not assumptions,
        "assumptions": assumptions,
    }


def describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {
        "n": len(v),
        "median": statistics.median(v),
        "mean": statistics.fmean(v),
        "stdev": statistics.stdev(v) if len(v) > 1 else None,
        "min": v[0],
        "max": v[-1],
        "p25": v[max(0, round(0.25 * (len(v) - 1)))],
        "p75": v[max(0, round(0.75 * (len(v) - 1)))],
        "mean_absolute": statistics.fmean(abs(x) for x in v),
        "within_20_percent": sum(1 for x in v if abs(x) <= 20.0) / len(v),
        "within_50_percent": sum(1 for x in v if abs(x) <= 50.0) / len(v),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", type=Path, default=RESULTS / "survey.json")
    ap.add_argument("--work-dir", type=Path, default=REPO / ".tcia_work")
    ap.add_argument("--out", type=Path, default=RESULTS / "open_fallback.json")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    survey = json.loads(args.survey.read_text(encoding="utf-8"))
    targets = eligible(survey)
    print(f"{len(targets)} eligible series (covered by the open table, with a recorded CTDIvol)")

    client = TciaClient(cache_dir=args.work_dir / "cache")
    cases: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}

    for i, screen in enumerate(targets, 1):
        dest = args.work_dir / "series" / screen["series_uid"][-24:]
        if not dest.is_dir():
            if args.no_download:
                excluded["not_cached"] = excluded.get("not_cached", 0) + 1
                continue
            try:
                print(f"  [{i}/{len(targets)}] fetching {screen['model_name']} "
                      f"({screen.get('n_images')} images)", flush=True)
                client.download_series(screen["series_uid"], dest)
            except Exception as exc:  # pragma: no cover - network dependent
                excluded["download_failed"] = excluded.get("download_failed", 0) + 1
                print(f"      {type(exc).__name__}: {exc}", flush=True)
                continue
        try:
            series = load_series(dest)
            case = evaluate(screen, series, region_for(screen.get("body_part", "")))
        except CoefficientNotAvailable as exc:
            excluded["technique_not_tabulated"] = excluded.get("technique_not_tabulated", 0) + 1
            print(f"      not tabulated: {exc}", flush=True)
            continue
        except Exception as exc:
            excluded["parse_failed"] = excluded.get("parse_failed", 0) + 1
            print(f"      {type(exc).__name__}: {exc}", flush=True)
            continue
        if case is None:
            excluded["no_usable_recorded_value"] = excluded.get("no_usable_recorded_value", 0) + 1
            continue
        cases.append(case)

    vendors = sorted({c["vendor"] for c in cases})
    diagnostic = [c for c in cases if c["recorded_ctdivol_mgy"] >= DIAGNOSTIC_CTDIVOL_MGY]
    headline = [c for c in diagnostic if c["collimation_measured"]]

    payload = {
        "provenance": {
            "generated_by": "tools/evaluate_open_fallback.py",
            "coefficients": provenance(),
            "case_selection": (
                "Every screened series whose scanner is in the open coefficient database, "
                "that carries a recorded CTDIvol to compare against, and whose acquisition "
                "parameters are complete. Not a sample: the eligible set is defined by the "
                "database's coverage, so there is no cap and no sampling seed."
            ),
            "ge_note": (
                "GE is the best-covered vendor in the open database but contributes no "
                "case here, because no GE series in the archive carries a recorded "
                "CTDIvol to validate against. GE is not excluded; the archive offers no "
                "reference for it."
            ),
        },
        "summary": {
            "n_eligible": len(targets),
            "n_evaluated": len(cases),
            "by_vendor": {v: sum(1 for c in cases if c["vendor"] == v) for v in vendors},
            "n_exact_table_match": sum(1 for c in cases if c["exact_table_match"]),
            "overall": describe([c["percent_error"] for c in cases]),
            "diagnostic_only": describe([c["percent_error"] for c in diagnostic]),
            "diagnostic_and_collimation_measured": {
                **describe([c["percent_error"] for c in headline]),
                "by_vendor": {
                    v: describe([c["percent_error"] for c in headline if c["vendor"] == v])
                    for v in vendors
                },
            },
            "exclusions": excluded,
        },
        "cases": cases,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    s = payload["summary"]
    print(f"\nevaluated {s['n_evaluated']} of {s['n_eligible']}  {s['by_vendor']}")
    print(f"exact table matches: {s['n_exact_table_match']}")
    for name in ("overall", "diagnostic_only", "diagnostic_and_collimation_measured"):
        b = s[name]
        if b.get("n"):
            print(f"  {name:38s} n={b['n']:3d}  median {b['median']:+7.1f}%  "
                  f"IQR {b['p25']:+.1f}..{b['p75']:+.1f}  within20 {b['within_20_percent']:.0%}")
    if excluded:
        print(f"  exclusions: {excluded}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
