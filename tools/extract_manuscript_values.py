"""Collect every number the manuscript is allowed to quote, from ``results/*.json``.

The manuscript must not contain a figure that is not in this file, and this file must
not contain a figure that was not computed by the tools in ``tools/``. That is the whole
anti-fabrication mechanism: ``tests/test_manuscript_consistency.py`` re-derives each
value from ``results/`` and asserts the formatted string appears in the manuscript.

Usage::

    python tools/extract_manuscript_values.py          # writes paper/manuscript_values.json
    python tools/extract_manuscript_values.py --print  # human-readable dump
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"


def _load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def _pct(x: float | None) -> str:
    """Format a fraction as a percentage the way the manuscript writes it."""
    return "n/a" if x is None else f"{100.0 * x:.1f}"


def _sig(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def collect() -> dict[str, Any]:
    layer1 = _load("rdsr_layer1.json")
    survey = _load("survey.json")

    s1 = layer1["summary"]
    files = [f for f in layer1["files"] if f.get("is_ct_rdsr")]
    events = layer1["events"]

    ctdi_values = [e["ctdivol_mgy"] for e in events if e["ctdivol_mgy"] is not None]
    dlp_values = [e["dlp_mgycm"] for e in events if e["dlp_mgycm"] is not None]

    absence = survey["tag_absence"]
    fallback = survey["fallback_error"]
    headline = fallback["diagnostic_and_collimation_measured"]

    out: dict[str, Any] = {
        "_provenance": {
            "generated_by": "tools/extract_manuscript_values.py",
            "inputs": ["results/rdsr_layer1.json", "results/survey.json"],
            "rule": (
                "Every number in the manuscript must appear here, and every number here "
                "must come from results/. Enforced by tests/test_manuscript_consistency.py."
            ),
        },
        "layer1": {
            "files_seen": s1["files_seen"],
            "files_parsed": s1["files_parsed_as_ct_rdsr"],
            "files_rejected": s1["files_rejected"],
            "n_vendors": len(s1["vendors"]),
            "vendors": s1["vendors"],
            "n_models": len({f["model"] for f in files}),
            "models": sorted({f["model"] for f in files}),
            "total_events": s1["total_irradiation_events"],
            "events_with_ctdivol": s1["events_with_ctdivol"],
            "events_with_dlp": s1["events_with_dlp"],
            "phantoms_observed": s1["phantoms_observed"],
            "ctdivol_min": min(ctdi_values),
            "ctdivol_max": max(ctdi_values),
            "dlp_min": min(dlp_values),
            "dlp_max": max(dlp_values),
            "dlp_sum_check_comparable": s1["dlp_sum_check"]["files_comparable"],
            "dlp_sum_check_within": s1["dlp_sum_check"]["files_within_tolerance"],
            "dlp_sum_tolerance_pct": _sig(100 * s1["dlp_sum_check"]["tolerance"], 0),
            "event_count_declared": s1["event_count_check"]["files_declaring_a_count"],
            "event_count_matching": s1["event_count_check"]["files_matching"],
            "by_vendor": {
                v: {
                    "files": b["files"],
                    "events": b["events"],
                    "events_with_ctdivol": b["events_with_ctdivol"],
                    "models": b["models"],
                    "phantom_cm": b["phantom_cm"],
                }
                for v, b in s1["by_vendor"].items()
            },
        },
        "survey_a": {
            "n_screened": absence["overall"]["n_series_screened"],
            "n_readable": absence["overall"]["n_series_readable"],
            "per_vendor_n": {
                v: b["n_series_readable"] for v, b in absence["by_vendor"].items()
            },
            "ctdivol_absence_pct": {
                v: _pct(b["ctdivol_absence_rate"]) for v, b in absence["by_vendor"].items()
            },
            "ctdivol_absence_pct_overall": _pct(absence["overall"]["ctdivol_absence_rate"]),
            "with_ctdivol": {
                v: b["with_ctdivol_tag"] for v, b in absence["by_vendor"].items()
            },
            "with_ctdivol_overall": absence["overall"]["with_ctdivol_tag"],
            "dlp_absence_pct_overall": _pct(absence["overall"]["dlp_absence_rate"]),
            "fallback_feasible_pct": {
                v: _pct(b["fallback_feasibility_rate"]) for v, b in absence["by_vendor"].items()
            },
            "fallback_feasible_pct_overall": _pct(
                absence["overall"]["fallback_feasibility_rate"]
            ),
            "surveyable_pct": {
                v: _pct(b["surveyable_rate"]) for v, b in absence["by_vendor"].items()
            },
            "surveyable_pct_overall": _pct(absence["overall"]["surveyable_rate"]),
            "n_collections": len(absence["overall"]["collections"]),
            "model_resolution_overall": absence["overall"]["model_resolution"],
            "unmatched_models_top": list(absence["unmatched_models"].items())[:8],
            "n_series_without_licence": absence["overall"]["n_series_without_licence"],
            "licences": absence["overall"]["licences"],
        },
        "survey_b": {
            "n_cases": fallback["overall"]["n"],
            "n_distinct_pairs": fallback["independence"][
                "n_distinct_recorded_estimated_pairs"
            ],
            "overall_median": _sig(fallback["overall"]["median"]),
            "overall_min": _sig(fallback["overall"]["min"]),
            "overall_max": _sig(fallback["overall"]["max"]),
            "collimation_measured_n": fallback["n_collimation_measured"],
            "collimation_reconstructed_n": fallback["n_collimation_reconstructed"],
            "collimation_measured_median": _sig(
                fallback["by_collimation_provenance"]["measured"]["median"]
            ),
            "collimation_reconstructed_median": _sig(
                fallback["by_collimation_provenance"]["reconstructed"]["median"]
            ),
            "vendor_by_collimation": fallback["vendor_by_collimation_provenance"],
            "headline_n": headline["n"],
            "headline_median": _sig(headline["median"]),
            "headline_mean_absolute": _sig(headline["mean_absolute"]),
            "headline_p25": _sig(headline["p25"]),
            "headline_p75": _sig(headline["p75"]),
            "headline_min": _sig(headline["min"]),
            "headline_max": _sig(headline["max"]),
            "headline_within_20_pct": _pct(headline["within_20_percent"]),
            "headline_within_50_pct": _pct(headline["within_50_percent"]),
            "headline_by_vendor": {
                v: {
                    "n": d["n"],
                    "median": _sig(d["median"]) if d["n"] else None,
                    "mean_absolute": _sig(d["mean_absolute"]) if d["n"] else None,
                }
                for v, d in headline["by_vendor"].items()
            },
            "exclusions": survey["fallback_exclusions"],
            "diagnostic_threshold_mgy": headline_threshold(fallback),
        },
        "survey_parameters": {
            "seed": survey["parameters"]["seed"],
            "screen_per_vendor": survey["parameters"]["screen_per_vendor"],
            "fallback_per_vendor": survey["parameters"]["fallback_per_vendor"],
            "max_images": survey["parameters"]["max_images_for_download"],
            "api": survey["parameters"]["api"],
            "access_started_utc": survey["provenance"]["access_started_utc"],
            "access_finished_utc": survey["provenance"]["access_finished_utc"],
        },
    }
    return out


def headline_threshold(fallback: dict) -> float:
    return fallback["diagnostic_acquisitions_only"]["threshold_mgy"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO / "paper" / "manuscript_values.json")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args()

    values = collect()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.show:
        print(json.dumps(values, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
