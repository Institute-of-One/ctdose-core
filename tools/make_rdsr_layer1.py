"""Build the Layer-1 validation table: RDSR parsing across four vendors.

Layer 1 of the validation establishes that the parser reads the *recorded* dose
correctly from real vendor objects. It is deliberately separate from Layer 2 (the
public-archive survey), which measures what happens when no such record exists.

For every CT Radiation Dose SR in the sample directory this writes one row per
irradiation event plus one summary row per file:

    vendor, model, event count, per-event CTDIvol, per-event DLP,
    accumulated DLP (recorded) vs summed per-event DLP, CTDIw reference phantom

Two internal consistency checks are computed and reported rather than asserted, so
that a vendor object which genuinely disagrees is visible instead of silently
dropped:

* ``dlp_sum_vs_total`` -- Sigma(event DLP) against the recorded accumulated DLP;
* ``events_declared_vs_parsed`` -- the SR's own irradiation-event count against the
  number of CT Acquisition containers actually parsed.

Usage::

    CTDOSE_RDSR_SAMPLES=/path/to/samples python tools/make_rdsr_layer1.py
    python tools/make_rdsr_layer1.py /path/to/samples --out results

The sample objects are third-party (OpenREM, GPL-3.0) and are **not** vendored into
this MIT repository; only the derived measurement table is written, with the source
attributed in its provenance block.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from pathlib import Path
from typing import Any

import pydicom

from ctdose_core.rdsr import phantom_cm, read_rdsr

#: Vendor label normalisation, so GE's several spellings collapse to one column.
VENDOR_ALIASES = {
    "GE": "GE",
    "GE MEDICAL SYSTEMS": "GE",
    "GE HEALTHCARE": "GE",
    "SIEMENS": "Siemens",
    "SIEMENS HEALTHINEERS": "Siemens",
    "TOSHIBA": "Canon/Toshiba",
    "TOSHIBA MEDICAL SYSTEMS": "Canon/Toshiba",
    "CANON MEDICAL SYSTEMS": "Canon/Toshiba",
    "PHILIPS": "Philips",
    "PHILIPS MEDICAL SYSTEMS": "Philips",
}

#: Relative tolerance for Sigma(event DLP) against the recorded accumulated DLP. The
#: values are quoted to a few significant figures, and events without a DLP legitimately
#: contribute nothing, so exact equality is not expected.
DLP_SUM_TOLERANCE = 0.01

C_TOTAL_EVENTS = "113812"


def normalise_vendor(manufacturer: str) -> str:
    key = str(manufacturer).strip().upper()
    if key in VENDOR_ALIASES:
        return VENDOR_ALIASES[key]
    for alias, label in VENDOR_ALIASES.items():
        if key.startswith(alias):
            return label
    return manufacturer.strip() or "unknown"


def declared_event_count(path: Path) -> int | None:
    """The SR's own 'Total Number of Irradiation Events', read independently."""
    ds = pydicom.dcmread(str(path), force=True)

    def walk(node: Any) -> int | None:
        for child in getattr(node, "ContentSequence", []) or []:
            seq = getattr(child, "ConceptNameCodeSequence", None)
            if seq and str(getattr(seq[0], "CodeValue", "")) == C_TOTAL_EVENTS:
                mvs = getattr(child, "MeasuredValueSequence", None)
                if mvs:
                    try:
                        return int(float(mvs[0].NumericValue))
                    except (TypeError, ValueError, AttributeError):
                        return None
            found = walk(child)
            if found is not None:
                return found
        return None

    return walk(ds)


def build_rows(sample_dir: Path) -> tuple[list[dict], list[dict]]:
    """Return ``(file_rows, event_rows)`` for every CT RDSR in ``sample_dir``."""
    file_rows: list[dict] = []
    event_rows: list[dict] = []

    for f in sorted(glob.glob(str(sample_dir / "*.dcm"))):
        path = Path(f)
        report = read_rdsr(path)
        if report is None:
            file_rows.append(
                {
                    "file": path.name,
                    "is_ct_rdsr": False,
                    "note": "not a CT Radiation Dose SR (e.g. a secondary-capture dose screen)",
                }
            )
            continue

        vendor = normalise_vendor(report.manufacturer)
        summed = report.summed_event_dlp
        total = float(report.total_dlp) if report.total_dlp else None
        declared = declared_event_count(path)

        ctdi_values = [float(a.ctdivol) for a in report.acquisitions if a.ctdivol]
        dlp_values = [float(a.dlp) for a in report.acquisitions if a.dlp]
        phantoms = sorted(
            {
                f"{a.phantom_type} ({phantom_cm(a.phantom_type)} cm)"
                for a in report.acquisitions
                if a.phantom_type
            }
        )

        dlp_check: dict[str, Any] = {"comparable": summed is not None and total not in (None, 0)}
        if dlp_check["comparable"]:
            dlp_check["summed_event_dlp_mgycm"] = summed
            dlp_check["recorded_total_dlp_mgycm"] = total
            dlp_check["relative_difference"] = (summed - total) / total
            dlp_check["within_tolerance"] = abs(summed - total) / total <= DLP_SUM_TOLERANCE
            dlp_check["events_missing_dlp"] = len(report.acquisitions) - len(dlp_values)

        file_rows.append(
            {
                "file": path.name,
                "is_ct_rdsr": True,
                "vendor": vendor,
                "manufacturer_raw": report.manufacturer,
                "model": report.model_name,
                "n_events_parsed": len(report.acquisitions),
                "n_events_declared": declared,
                "events_declared_vs_parsed": (
                    None if declared is None else declared == len(report.acquisitions)
                ),
                "n_events_with_ctdivol": len(ctdi_values),
                "n_events_with_dlp": len(dlp_values),
                "ctdivol_min_mgy": min(ctdi_values) if ctdi_values else None,
                "ctdivol_max_mgy": max(ctdi_values) if ctdi_values else None,
                "ctdivol_values_mgy": ctdi_values,
                "dlp_values_mgycm": dlp_values,
                "summed_event_dlp_mgycm": summed,
                "recorded_total_dlp_mgycm": total,
                "dlp_sum_vs_total": dlp_check,
                "ctdiw_phantoms": phantoms,
                "phantom_cm": sorted(
                    {
                        c
                        for c in (phantom_cm(a.phantom_type) for a in report.acquisitions)
                        if c is not None
                    }
                ),
            }
        )

        for i, a in enumerate(report.acquisitions):
            event_rows.append(
                {
                    "file": path.name,
                    "vendor": vendor,
                    "model": report.model_name,
                    "event_index": i,
                    "protocol": a.protocol,
                    "acquisition_type": a.acquisition_type,
                    "target_region": a.target_region,
                    "ctdivol_mgy": float(a.ctdivol) if a.ctdivol else None,
                    "dlp_mgycm": float(a.dlp) if a.dlp else None,
                    "ctdiw_phantom": a.phantom_type,
                    "phantom_cm": phantom_cm(a.phantom_type),
                    "kvp": a.kvp,
                    "mean_tube_current_ma": a.mean_tube_current_ma,
                    "exposure_time_per_rotation_s": a.exposure_time_per_rotation_s,
                    "pitch": a.pitch,
                    "total_collimation_mm": a.total_collimation_mm,
                    "scanning_length_mm": a.scanning_length_mm,
                    "modulation_type": a.modulation_type,
                }
            )

    return file_rows, event_rows


def summarise(file_rows: list[dict]) -> dict[str, Any]:
    parsed = [r for r in file_rows if r.get("is_ct_rdsr")]
    by_vendor: dict[str, dict[str, Any]] = {}
    for r in parsed:
        v = by_vendor.setdefault(
            r["vendor"],
            {"files": 0, "events": 0, "events_with_ctdivol": 0, "models": set(), "phantom_cm": set()},
        )
        v["files"] += 1
        v["events"] += r["n_events_parsed"]
        v["events_with_ctdivol"] += r["n_events_with_ctdivol"]
        v["models"].add(r["model"])
        v["phantom_cm"].update(r["phantom_cm"])
    for v in by_vendor.values():
        v["models"] = sorted(v["models"])
        v["phantom_cm"] = sorted(v["phantom_cm"])

    checked = [r for r in parsed if r["dlp_sum_vs_total"].get("comparable")]
    return {
        "files_seen": len(file_rows),
        "files_parsed_as_ct_rdsr": len(parsed),
        "files_rejected": len(file_rows) - len(parsed),
        "vendors": sorted(by_vendor),
        "total_irradiation_events": sum(r["n_events_parsed"] for r in parsed),
        "events_with_ctdivol": sum(r["n_events_with_ctdivol"] for r in parsed),
        "events_with_dlp": sum(r["n_events_with_dlp"] for r in parsed),
        "phantoms_observed": sorted({c for r in parsed for c in r["phantom_cm"]}),
        "dlp_sum_check": {
            "files_comparable": len(checked),
            "files_within_tolerance": sum(
                1 for r in checked if r["dlp_sum_vs_total"]["within_tolerance"]
            ),
            "tolerance": DLP_SUM_TOLERANCE,
        },
        "event_count_check": {
            "files_declaring_a_count": sum(
                1 for r in parsed if r["n_events_declared"] is not None
            ),
            "files_matching": sum(1 for r in parsed if r["events_declared_vs_parsed"] is True),
        },
        "by_vendor": by_vendor,
    }


CSV_COLUMNS = [
    "file",
    "vendor",
    "model",
    "n_events_parsed",
    "n_events_declared",
    "n_events_with_ctdivol",
    "ctdivol_min_mgy",
    "ctdivol_max_mgy",
    "summed_event_dlp_mgycm",
    "recorded_total_dlp_mgycm",
    "dlp_sum_relative_difference",
    "phantom_cm",
]


def write_csv(path: Path, file_rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in file_rows:
            if not r.get("is_ct_rdsr"):
                continue
            w.writerow(
                {
                    **{k: r.get(k) for k in CSV_COLUMNS if k not in ("phantom_cm", "dlp_sum_relative_difference")},
                    "phantom_cm": "/".join(str(c) for c in r["phantom_cm"]),
                    "dlp_sum_relative_difference": r["dlp_sum_vs_total"].get("relative_difference"),
                }
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[1]
    ap.add_argument(
        "samples",
        nargs="?",
        default=os.environ.get("CTDOSE_RDSR_SAMPLES", ""),
        help="directory of CT Radiation Dose SR objects",
    )
    ap.add_argument("--out", type=Path, default=repo / "results")
    args = ap.parse_args()

    if not args.samples or not Path(args.samples).is_dir():
        print("set CTDOSE_RDSR_SAMPLES or pass a directory of CT RDSR objects")
        return 2
    sample_dir = Path(args.samples)

    file_rows, event_rows = build_rows(sample_dir)
    summary = summarise(file_rows)
    args.out.mkdir(parents=True, exist_ok=True)

    payload = {
        "provenance": {
            "layer": "1 - recorded-dose parsing against real vendor objects",
            "generated_by": "tools/make_rdsr_layer1.py",
            "parser": "ctdose_core.rdsr (DICOM PS3.16 TID 10011-10013 coded content items)",
            "sample_source": (
                "OpenREM test objects (GPL-3.0), openrem/remapp/tests/test_files/CT-*.dcm. "
                "The objects themselves are third-party and are not redistributed here; "
                "this table holds only measurements read out of them."
            ),
            "sample_directory": str(sample_dir),
            "notes": [
                "Sigma(event DLP) is compared against the SR's own accumulated DLP; a "
                "difference is reported, never corrected.",
                "The irradiation-event count declared by the SR is read by an "
                "independent tree walk (declared_event_count) rather than by the parser.",
            ],
        },
        "summary": summary,
        "files": file_rows,
        "events": event_rows,
    }
    (args.out / "rdsr_layer1.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_csv(args.out / "rdsr_layer1.csv", file_rows)

    print(f"files seen            : {summary['files_seen']}")
    print(f"parsed as CT RDSR     : {summary['files_parsed_as_ct_rdsr']}")
    print(f"vendors               : {', '.join(summary['vendors'])}")
    print(f"irradiation events    : {summary['total_irradiation_events']}")
    print(f"events with CTDIvol   : {summary['events_with_ctdivol']}")
    print(f"phantoms observed (cm): {summary['phantoms_observed']}")
    c = summary["dlp_sum_check"]
    print(
        f"Sigma(event DLP) vs total : {c['files_within_tolerance']}/{c['files_comparable']} "
        f"within {c['tolerance']:.0%}"
    )
    e = summary["event_count_check"]
    print(f"declared event count  : {e['files_matching']}/{e['files_declaring_a_count']} match")
    print(f"wrote {args.out / 'rdsr_layer1.json'} and .csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
