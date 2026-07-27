"""Compute SSDE and effective dose for the evaluated public-archive series.

The survey (``tools/run_tcia_survey.py``) answers whether a dose index exists and how well
it can be reconstructed. It does not exercise the size-correction or effective-dose layers,
which leaves those methods described but unvalidated. This script closes that gap on the
same series, using the CT volumes already retrieved by the survey.

For every case in ``results/survey.json`` whose images are available locally it computes:

* the water-equivalent diameter (AAPM Report 220) from the automatically extracted body
  contour, taken as the median over the central slices;
* the AAPM TG-204 conversion factor and the resulting SSDE, against **both** the recorded
  and the reconstructed CTDIvol, so the size correction and the reconstruction error stay
  separable;
* the effective dose by whichever route the scanner's coefficient row prescribes.

Usage::

    python tools/make_size_and_dose_layer.py --series-root .tcia_work/series

Writes ``results/size_and_dose.json``. Series whose images are not present locally are
skipped and counted, never imputed.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any

from ctdose_core.effdose import effective_dose
from ctdose_core.io import load_series
from ctdose_core.metrics import Acquisition, acquisition_from_series, estimate_ctdivol
from ctdose_core.pipeline import measure_patient_size
from ctdose_core.provenance import Quantity, Source
from ctdose_core.ssde import conversion_factor, phantom_for, ssde
from ctdose_core.survey import DIAGNOSTIC_CTDIVOL_MGY
from ctdose_core.tables import lookup_conversion, lookup_scanner

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"

#: A series with fewer slices than this is a localiser, a fragment or a single
#: reconstructed image, not a diagnostic acquisition: its scan range is ~0, so the DLP and
#: effective dose derived from it are meaningless.
MIN_DIAGNOSTIC_SLICES = 10

#: Public archives contain scans of physical test objects alongside patient studies. A
#: radiomics or dosimetry phantom has a perfectly valid water-equivalent diameter, but it
#: is not a patient size and must not enter a patient-size distribution. Detected from the
#: collection name, which is how the archive labels them.
PHANTOM_COLLECTION_MARKERS = ("phantom", "qiba")


def is_phantom_collection(collection: str) -> bool:
    name = collection.lower()
    return any(marker in name for marker in PHANTOM_COLLECTION_MARKERS)


def _describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "p25": ordered[max(0, round(0.25 * (len(ordered) - 1)))],
        "p75": ordered[max(0, round(0.75 * (len(ordered) - 1)))],
    }


def evaluate_case(case: dict, series_root: Path) -> dict[str, Any] | None:
    """Size-correct and dose-convert one survey case; ``None`` if images are unavailable."""
    directory = series_root / case["series_uid"][-24:]
    if not directory.is_dir() or not any(directory.iterdir()):
        return None

    series = load_series(directory)
    diameter, detail = measure_patient_size(series)
    if diameter is None:
        return {
            "series_uid": case["series_uid"],
            "vendor": case["vendor"],
            "collection": case["collection"],
            "skipped": detail.get("reason", "no contour"),
        }

    region = case["region"]
    acq: Acquisition = acquisition_from_series(series)
    coef = lookup_scanner(case["table_model"], region, "Adults")
    factors = lookup_conversion(region, "Adults", series.patient_sex or "F")

    recorded = Quantity(
        value=case["recorded_ctdivol_mgy"], unit="mGy", source=Source.DICOM_TAG,
        formula="CTDI Volume (0018,9345)",
    )
    reconstructed = estimate_ctdivol(acq, coef)
    phantom = phantom_for(region, float(diameter))
    factor = conversion_factor(float(diameter), phantom, region="body")

    ssde_recorded = ssde(recorded, diameter, phantom=phantom, region="body")
    ssde_reconstructed = ssde(reconstructed, diameter, phantom=phantom, region="body")

    row: dict[str, Any] = {
        "series_uid": case["series_uid"],
        "vendor": case["vendor"],
        "collection": case["collection"],
        "table_model": case["table_model"],
        "region": region,
        "n_slices": series.n_slices,
        "is_phantom_collection": is_phantom_collection(case["collection"]),
        "too_few_slices": series.n_slices < MIN_DIAGNOSTIC_SLICES,
        "diagnostic_ctdivol": case["recorded_ctdivol_mgy"] >= DIAGNOSTIC_CTDIVOL_MGY,
        # A scan range of zero across many slices means the archive has flattened the
        # z coordinate. SSDE is unaffected (it has no z dependence); DLP and effective
        # dose are not recoverable and must not be reported for these series.
        "scan_range_mm": series.scan_range_mm,
        "usable_scan_range": bool(series.n_slices <= 1 or series.scan_range_mm > 1.0),
        "collimation_measured": case["collimation_measured"],
        "recorded_ctdivol_mgy": case["recorded_ctdivol_mgy"],
        "reconstructed_ctdivol_mgy": float(reconstructed),
        "water_equivalent_diameter_cm": float(diameter),
        "wed_slice_spread_cm": (
            max(detail["per_slice_dw_cm"]) - min(detail["per_slice_dw_cm"])
            if detail.get("per_slice_dw_cm") else None
        ),
        "phantom_cm": phantom,
        "ssde_conversion_factor": float(factor),
        "ssde_from_recorded_mgy": float(ssde_recorded.ssde),
        "ssde_from_reconstructed_mgy": float(ssde_reconstructed.ssde),
        "ssde_to_ctdivol_ratio": float(ssde_recorded.ssde) / case["recorded_ctdivol_mgy"],
    }

    # Effective dose needs a DLP, and DLP needs a scan range. Where the archive has
    # flattened the z coordinate there is nothing to compute from, and reporting a number
    # would be worse than reporting none.
    if not row["usable_scan_range"]:
        row["effective_dose_msv"] = None
        row["effective_dose_note"] = (
            "scan range unrecoverable: all slices report the same longitudinal position"
        )
        return row

    try:
        from ctdose_core.metrics import dlp_in_air, estimate_dlp

        dlp_recorded = estimate_dlp(acq, coef, recorded)
        e_recorded = effective_dose(
            coef, factors,
            dlp_air=dlp_in_air(acq, coef) if coef.nctdi_air is not None else None,
            dlp_mgycm=dlp_recorded, kvp=series.kvp,
        )
        row["dlp_from_recorded_mgycm"] = float(dlp_recorded)
        row["effective_dose_msv"] = float(e_recorded)
        row["effective_dose_route"] = coef.effective_dose_route
        row["effective_dose_formula"] = e_recorded.formula
    except (ValueError, LookupError) as exc:
        row["effective_dose_msv"] = None
        row["effective_dose_note"] = f"{type(exc).__name__}: {exc}"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", type=Path, default=RESULTS / "survey.json")
    ap.add_argument("--series-root", type=Path, default=REPO / ".tcia_work" / "series")
    ap.add_argument("--out", type=Path, default=RESULTS / "size_and_dose.json")
    args = ap.parse_args()

    survey = json.loads(args.survey.read_text(encoding="utf-8"))
    cases = survey["fallback_cases"]

    rows: list[dict[str, Any]] = []
    unavailable = 0
    for case in cases:
        result = evaluate_case(case, args.series_root)
        if result is None:
            unavailable += 1
            continue
        rows.append(result)
        if "skipped" in result:
            print(f"  {case['series_uid'][-12:]}: skipped ({result['skipped']})")
            continue
        e = result.get("effective_dose_msv")
        e_text = "E n/a" if e is None else f"E {e:6.2f} mSv"
        print(
            f"  {result['vendor']:14s} D_w {result['water_equivalent_diameter_cm']:5.1f} cm  "
            f"f {result['ssde_conversion_factor']:.2f}  "
            f"CTDIvol {result['recorded_ctdivol_mgy']:6.2f} -> "
            f"SSDE {result['ssde_from_recorded_mgy']:6.2f} mGy  {e_text}"
        )

    usable = [r for r in rows if "skipped" not in r]
    # Patient-like subset: excludes physical test objects, series too short to be a
    # diagnostic acquisition, and localiser-level recorded doses. Every excluded row stays
    # in "cases" with its flags, so the filtering is inspectable rather than implicit.
    patients = [
        r for r in usable
        if not r["is_phantom_collection"] and not r["too_few_slices"] and r["diagnostic_ctdivol"]
    ]
    payload = {
        "provenance": {
            "layer": "size correction and effective dose on the surveyed public series",
            "generated_by": "tools/make_size_and_dose_layer.py",
            "inputs": ["results/survey.json", "locally retrieved TCIA series"],
            "note": (
                "SSDE is reported from both the recorded and the reconstructed CTDIvol so "
                "that the size correction and the reconstruction error remain separable. "
                "Effective dose is a population estimate for a reference phantom, not an "
                "individual patient's dose."
            ),
            "inclusion_criteria": (
                f"The patient-like subset excludes collections whose name marks them as "
                f"physical test objects ({'/'.join(PHANTOM_COLLECTION_MARKERS)}), series "
                f"with fewer than {MIN_DIAGNOSTIC_SLICES} slices, and series whose recorded "
                f"CTDIvol is below {DIAGNOSTIC_CTDIVOL_MGY} mGy. Excluded rows are retained "
                f"in 'cases' with the flags that excluded them."
            ),
        },
        "summary": {
            "n_cases_in_survey": len(cases),
            "n_images_unavailable_locally": unavailable,
            "n_contour_failed": len(rows) - len(usable),
            "n_evaluated": len(usable),
            "n_phantom_collection": sum(1 for r in usable if r["is_phantom_collection"]),
            "n_too_few_slices": sum(1 for r in usable if r["too_few_slices"]),
            "n_below_diagnostic_ctdivol": sum(1 for r in usable if not r["diagnostic_ctdivol"]),
            "n_patient_like": len(patients),
            "n_scan_range_unrecoverable": sum(1 for r in usable if not r["usable_scan_range"]),
            "n_patient_like_with_scan_range": sum(1 for r in patients if r["usable_scan_range"]),
            "scan_range_note": (
                "SSDE has no longitudinal dependence and is reported for every patient-like "
                "series. DLP and effective dose require a scan range, and are reported only "
                "for the series whose longitudinal geometry survived de-identification."
            ),
            "all_evaluated": {
                "water_equivalent_diameter_cm": _describe(
                    [r["water_equivalent_diameter_cm"] for r in usable]
                ),
            },
            "patient_like": {
                "water_equivalent_diameter_cm": _describe(
                    [r["water_equivalent_diameter_cm"] for r in patients]
                ),
                "ssde_conversion_factor": _describe(
                    [r["ssde_conversion_factor"] for r in patients]
                ),
                "ssde_from_recorded_mgy": _describe(
                    [r["ssde_from_recorded_mgy"] for r in patients]
                ),
                "ssde_to_ctdivol_ratio": _describe([r["ssde_to_ctdivol_ratio"] for r in patients]),
                "effective_dose_msv": _describe(
                    [r["effective_dose_msv"] for r in patients if r.get("effective_dose_msv")]
                ),
                "phantoms_used": sorted({r["phantom_cm"] for r in patients}),
                "by_vendor_wed": {
                    v: _describe(
                        [r["water_equivalent_diameter_cm"] for r in patients if r["vendor"] == v]
                    )
                    for v in sorted({r["vendor"] for r in patients})
                },
                "by_collimation_effective_dose": {
                    "measured": _describe([
                        r["effective_dose_msv"] for r in patients
                        if r["collimation_measured"] and r.get("effective_dose_msv")
                    ]),
                    "reconstructed": _describe([
                        r["effective_dose_msv"] for r in patients
                        if not r["collimation_measured"] and r.get("effective_dose_msv")
                    ]),
                },
            },
        },
        "cases": rows,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with (RESULTS / "size_and_dose.csv").open("w", encoding="utf-8", newline="") as fh:
        cols = [
            "vendor", "collection", "table_model", "region", "n_slices",
            "is_phantom_collection", "too_few_slices", "diagnostic_ctdivol",
            "water_equivalent_diameter_cm", "phantom_cm", "ssde_conversion_factor",
            "recorded_ctdivol_mgy", "ssde_from_recorded_mgy",
            "reconstructed_ctdivol_mgy", "ssde_from_reconstructed_mgy",
            "ssde_to_ctdivol_ratio", "dlp_from_recorded_mgycm", "effective_dose_msv",
            "effective_dose_route", "series_uid",
        ]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in usable:
            w.writerow(r)

    s = payload["summary"]
    print(f"\nevaluated {s['n_evaluated']}/{s['n_cases_in_survey']} cases "
          f"({s['n_images_unavailable_locally']} images unavailable, "
          f"{s['n_contour_failed']} contour failures)")
    print(f"  excluded from the patient-like subset: "
          f"{s['n_phantom_collection']} physical test object(s), "
          f"{s['n_too_few_slices']} series under {MIN_DIAGNOSTIC_SLICES} slices, "
          f"{s['n_below_diagnostic_ctdivol']} below {DIAGNOSTIC_CTDIVOL_MGY} mGy")
    print(f"  patient-like n = {s['n_patient_like']}")
    for key in ("water_equivalent_diameter_cm", "ssde_conversion_factor",
                "ssde_from_recorded_mgy", "ssde_to_ctdivol_ratio", "effective_dose_msv"):
        d = s["patient_like"][key]
        if d["n"]:
            print(f"    {key:30s} n={d['n']:2d}  median {d['median']:7.2f}  "
                  f"IQR {d['p25']:.2f}..{d['p75']:.2f}  range {d['min']:.2f}..{d['max']:.2f}")
    print(f"wrote {args.out} and size_and_dose.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
