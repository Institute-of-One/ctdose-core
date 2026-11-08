"""Validate the open-table reconstruction on GE, using RDSRs as the reference.

The accuracy arm built from the public archive (``tools/evaluate_open_fallback.py``)
has no GE case, for a reason that is the subject of the paper rather than an oversight:
GE records CTDIvol in no sampled image header, so the archive offers nothing to compare
a GE prediction against. The reviewing section editor asked for either phantom scanning
or a source that carries RDSR for GE.

This is that source, and it was already in hand. Layer 1 parses three GE CT Radiation
Dose SRs, and an RDSR carries the scanner's **own** CTDIvol per irradiation event
alongside the technique that produced it. Reading the technique out of the report and
predicting the CTDIvol from the open coefficient table therefore tests the dose model on
GE without any phantom.

What this establishes, and what it does not
-------------------------------------------
It tests the **dose model**: table coefficient, mAs per rotation, pitch. It does *not*
test the **metadata-gap recovery path**, because the technique here is read from a
structured report rather than reconstructed from a de-identified image header. Those are
different claims and the manuscript must not merge them.

Eligibility is decided by named rules, applied before any prediction is attempted, so
that the comparison set is defined rather than being whatever the prediction happened
not to fail on. Every exclusion is counted and reported with its reason.

The decisive rule is ``helical``. The formula

    CTDIvol = CTDIw(model, kVp, collimation, phantom, bowtie) * (mAs_rot / 100) / pitch

models the dose index of one rotation's worth of output spread along the table feed. In a
stationary or cine acquisition the table does not move and the scanner accumulates
CTDIvol over the whole dwell, so ``exposure time per rotation`` no longer gives the
exposure that produced the recorded figure. Four of the GE events record a pitch of 0.100
with a scanning length of 0.0 mm, which is not a helical pitch at all. Predicting those
would compare a single-rotation model against an accumulated measurement and report the
difference as model error.

Usage::

    CTDOSE_RDSR_SAMPLES=/path/to/samples python tools/evaluate_ge_rdsr.py
    python tools/evaluate_ge_rdsr.py /path/to/samples --out results

Writes ``results/ge_rdsr_accuracy.json``. As in Layer 1, the sample objects are
third-party (OpenREM and PixelMed, GPL-3.0) and are **not** vendored into this MIT
repository; only the derived measurements are written.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from pathlib import Path
from typing import Any

from ctdose_core.ctdi_table import (
    CoefficientNotAvailable,
    lookup,
    models,
    provenance,
    resolve_model,
)
from ctdose_core.metrics import Acquisition, estimate_ctdivol_open
from ctdose_core.rdsr import RdsrAcquisition, phantom_cm, read_rdsr

#: Acquisition types whose recorded CTDIvol is the output of one rotation's worth of
#: exposure distributed along the table feed, which is what the formula models. The DICOM
#: code meanings come from CID 10013 "CT Acquisition Type".
HELICAL_TYPES = ("spiral acquisition",)

#: Acquisition types that irradiate without table feed. Their recorded CTDIvol accumulates
#: over the dwell, so mAs per rotation does not describe the exposure behind it.
NO_TABLE_FEED_TYPES = (
    "stationary acquisition",
    "sequenced acquisition",
    "constant angle acquisition",
    "free acquisition",
)


def _slug(text: str) -> str:
    return " ".join(str(text).lower().split())


def technique_complete(a: RdsrAcquisition) -> list[str]:
    """Names of the technique fields the reconstruction needs and the event lacks."""
    needed = {
        "tube potential": a.kvp,
        "mean tube current": a.mean_tube_current_ma,
        "exposure time per rotation": a.exposure_time_per_rotation_s,
        "pitch": a.pitch,
        "total collimation": a.total_collimation_mm,
    }
    missing = [name for name, value in needed.items() if value is None or value <= 0]
    if phantom_cm(a.phantom_type) is None:
        missing.append("CTDIw phantom code")
    return missing


def exclusion_reason(
    acq: RdsrAcquisition,
    *,
    resolved: tuple[str, str] | None,
    refusal: str = "",
) -> tuple[str, str]:
    """Why this event is not in the comparison, or ``("", "")`` if it is.

    The rules are applied in a fixed order and each returns its own reason, so an event
    is counted against the first rule it fails rather than against whichever check
    happened to raise. Having this as one function, outside the file loop, is also what
    lets the tests drive the rule set directly on synthetic events.
    """
    if not acq.ctdivol:
        return ("no recorded CTDIvol to compare against", "")
    if resolved is None:
        return ("scanner not in the open coefficient database", refusal)
    kind = _slug(acq.acquisition_type)
    if kind not in HELICAL_TYPES:
        return (
            (
                "no table feed, so the recorded CTDIvol accumulates over the dwell "
                "and mAs per rotation does not describe the exposure behind it"
            )
            if kind in NO_TABLE_FEED_TYPES
            else "acquisition type is not a helical scan",
            f"acquisition type {acq.acquisition_type!r}, pitch {acq.pitch!r}, "
            f"scanning length {acq.scanning_length_mm!r} mm",
        )
    missing = technique_complete(acq)
    if missing:
        return ("incomplete technique", "missing: " + ", ".join(missing))
    return ("", "")


def beam_width_consistency(acq: RdsrAcquisition) -> dict[str, Any]:
    """Is the recorded total collimation a plausible total beam width?

    The GE reports record 2.5 or 5.0 mm in DCM 113827 "Nominal Total Collimation Width"
    on 64-slice scanners whose widest beams are 40 mm. Rather than argue the point from
    the model name, derive it from the report's own numbers. In a helical scan the table
    advances ``collimation * pitch`` per rotation, so the recorded scanning length fixes
    the number of rotations and, with the recorded rotation time, the scan duration:

        rotations = scanning length / (collimation * pitch)
        duration  = rotations * exposure time per rotation

    A 468 mm helical pass at a true 2.5 mm beam would take roughly a hundred seconds of
    continuous rotation. The same pass at 40 mm takes a few seconds. The implausible
    figure is the evidence that the recorded field is not a beam width -- most likely a
    single detector row or the nominal reconstructed thickness -- and it is reported
    rather than corrected, because correcting it would be choosing the collimation that
    makes the prediction agree.
    """
    length = float(acq.scanning_length_mm or 0.0)
    pitch = float(acq.pitch)
    t_rot = float(acq.exposure_time_per_rotation_s)
    out: dict[str, Any] = {
        "scanning_length_mm": length,
        "formula": "rotations = L / (collimation * pitch); duration = rotations * t_rot",
        "at_collimation_mm": {},
    }
    if length <= 0:
        return out
    for coll in (float(acq.total_collimation_mm), 10.0, 20.0, 40.0):
        rotations = length / (coll * pitch)
        out["at_collimation_mm"][f"{coll:g}"] = {
            "rotations": rotations,
            "scan_duration_s": rotations * t_rot,
            "is_the_recorded_value": coll == float(acq.total_collimation_mm),
        }
    return out


def table_profile(vendor: str, model: str, *, kvp: float, phantom: int) -> dict[str, Any]:
    """The table's coefficient at every tabulated collimation for this technique.

    The GE reports record a 2.5 or 5.0 mm total collimation on 64-slice scanners, while
    the narrowest beam tabulated for those two models is 10 mm. Whether that is a
    genuinely narrow beam or a vendor writing a single detector row into the
    total-collimation field changes the prediction substantially, so the alternatives are
    printed rather than argued about.

    The narrowest row is not 10 mm for every GE model: Revolution CT ES is tabulated at
    5 mm. A report from that scanner recording 5 mm would match exactly, raise no
    assumption, and so hide the same recording defect behind an apparently clean lookup.
    That is why ``beam_width_consistency`` tests the recorded width against the scan
    duration rather than against whether the table happened to have a matching row.
    """
    match = next((m for m in models() if m.vendor == vendor and m.model == model), None)
    if match is None:
        return {}
    out: dict[str, Any] = {}
    colls = sorted({r["collimation_mm"] for r in match.rows if r["phantom_cm"] == phantom})
    for coll in colls:
        try:
            hit = lookup(
                vendor, model, kvp=kvp, collimation_mm=coll, phantom_cm=phantom
            )
        except CoefficientNotAvailable:
            continue
        out[f"{coll:g}mm"] = {
            "nctdi_w_mgy_per_100mas": hit.nctdi_w_mgy_per_100mas,
            "bowtie": hit.bowtie,
        }
    return out


def evaluate_event(
    *,
    file_name: str,
    vendor: str,
    model: str,
    manufacturer_raw: str,
    model_raw: str,
    index: int,
    acq: RdsrAcquisition,
) -> dict[str, Any]:
    """Predict one event's CTDIvol and compare it with the figure the scanner recorded."""
    recorded = float(acq.ctdivol)
    phantom = phantom_cm(acq.phantom_type)
    mas_rot = float(acq.mean_tube_current_ma) * float(acq.exposure_time_per_rotation_s)

    # slice_thickness_mm and scan_range_mm are validated by Acquisition but are not
    # inputs to estimate_ctdivol_open; check_formula_inputs() below proves that rather
    # than asserting it, so that a future change to the formula cannot silently start
    # depending on a value this script invented.
    geometry = Acquisition(
        mas_per_rotation=mas_rot,
        collimation_mm=float(acq.total_collimation_mm),
        pitch=float(acq.pitch),
        slice_thickness_mm=float(acq.total_collimation_mm),
        scan_range_mm=float(acq.scanning_length_mm or 0.0),
        kvp=float(acq.kvp),
    )
    est = estimate_ctdivol_open(geometry, vendor=vendor, model=model, phantom_cm=phantom)
    estimated = float(est)
    assumptions = [n for n in est.notes if not n.startswith("reconstructed because")]

    return {
        "file": file_name,
        "event_index": index,
        "manufacturer_raw": manufacturer_raw,
        "model_raw": model_raw,
        "table_vendor": vendor,
        "table_model": model,
        "acquisition_type": acq.acquisition_type,
        "protocol": acq.protocol,
        "phantom_cm": phantom,
        "kvp": float(acq.kvp),
        "mean_tube_current_ma": float(acq.mean_tube_current_ma),
        "exposure_time_per_rotation_s": float(acq.exposure_time_per_rotation_s),
        "mas_per_rotation": mas_rot,
        "pitch": float(acq.pitch),
        "recorded_collimation_mm": float(acq.total_collimation_mm),
        "scanning_length_mm": acq.scanning_length_mm,
        "modulation_type": acq.modulation_type,
        "recorded_ctdivol_mgy": recorded,
        "estimated_ctdivol_mgy": estimated,
        "percent_error": 100.0 * (estimated - recorded) / recorded,
        "table_ctdiw_mgy_per_100mas": est.inputs["CTDIw_mGy_per_100mAs"],
        "table_collimation_mm": est.inputs["table_collimation_mm"],
        "table_kvp": est.inputs["table_kvp"],
        "table_bowtie": est.inputs["table_bowtie"],
        "exact_table_match": not assumptions,
        "assumptions": assumptions,
        "implied_nctdi_w_mgy_per_100mas": recorded * float(acq.pitch) / (mas_rot / 100.0),
        "table_at_every_collimation": table_profile(
            vendor, model, kvp=float(acq.kvp), phantom=phantom
        ),
        "beam_width_consistency": beam_width_consistency(acq),
    }


def check_formula_inputs() -> dict[str, Any]:
    """Prove the prediction ignores the two fields this script had to invent.

    ``Acquisition`` requires a slice thickness and a scan range. Neither is recorded in
    an RDSR and neither appears in the CTDIvol formula, so this script passes the
    collimation and the scanning length. If a future change made the estimate depend on
    either, every number here would quietly acquire a made-up input. Perturb them and
    require the estimate not to move.
    """
    base = dict(
        mas_per_rotation=60.0, collimation_mm=20.0, pitch=1.0, kvp=120.0
    )
    a = Acquisition(slice_thickness_mm=5.0, scan_range_mm=100.0, **base)
    b = Acquisition(slice_thickness_mm=0.625, scan_range_mm=700.0, **base)
    va = float(estimate_ctdivol_open(a, vendor="GE", model="LightSpeed VCT", phantom_cm=32))
    vb = float(estimate_ctdivol_open(b, vendor="GE", model="LightSpeed VCT", phantom_cm=32))
    return {
        "question": (
            "does estimate_ctdivol_open depend on slice thickness or scan range, which "
            "an RDSR does not record and this script therefore supplies?"
        ),
        "estimate_with_5mm_slice_100mm_range": va,
        "estimate_with_0.625mm_slice_700mm_range": vb,
        "invariant": va == vb,
    }


def collimation_sensitivity(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """What the comparison would give at each tabulated collimation instead.

    This is a sensitivity analysis, not an alternative result. The headline uses the
    collimation the report records, because that is what a reader of these objects has.
    Reporting the alternatives shows how much of the residual is the coefficient lookup
    rather than the dose model -- and selecting whichever row agreed best would be
    fitting one free parameter to four observations.
    """
    out: dict[str, Any] = {
        "note": (
            "sensitivity only; the headline result uses the collimation the RDSR "
            "records. Choosing the best-agreeing row would fit a free parameter."
        ),
        "by_collimation_mm": {},
    }
    for coll in ("10mm", "20mm", "40mm"):
        errors = []
        for c in cases:
            entry = c["table_at_every_collimation"].get(coll)
            if not entry:
                continue
            implied = c["implied_nctdi_w_mgy_per_100mas"]
            errors.append(100.0 * (entry["nctdi_w_mgy_per_100mas"] / implied - 1.0))
        if errors:
            out["by_collimation_mm"][coll] = describe(errors)
    return out


def describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    v = sorted(values)
    out: dict[str, Any] = {
        "n": len(v),
        "median": statistics.median(v),
        "mean": statistics.fmean(v),
        "min": v[0],
        "max": v[-1],
        "mean_absolute": statistics.fmean(abs(x) for x in v),
        "within_20_percent": sum(1 for x in v if abs(x) <= 20.0) / len(v),
        "within_50_percent": sum(1 for x in v if abs(x) <= 50.0) / len(v),
    }
    if len(v) > 1:
        out["stdev"] = statistics.stdev(v)
    return out


def run(sample_dir: Path) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    for f in sorted(glob.glob(str(sample_dir / "*.dcm"))):
        path = Path(f)
        report = read_rdsr(path)
        if report is None:
            continue
        if "ge" not in _slug(report.manufacturer).replace(" ", "")[:2]:
            continue

        resolved: tuple[str, str] | None
        refusal = ""
        try:
            resolved = resolve_model(report.manufacturer, report.model_name)
        except CoefficientNotAvailable as exc:
            resolved = None
            refusal = str(exc)

        files.append(
            {
                "file": path.name,
                "manufacturer_raw": report.manufacturer,
                "model_raw": report.model_name,
                "n_events": len(report.acquisitions),
                "n_events_with_ctdivol": sum(1 for a in report.acquisitions if a.ctdivol),
                "table_model": f"{resolved[0]} {resolved[1]}" if resolved else None,
                "model_refusal": refusal,
            }
        )

        for i, acq in enumerate(report.acquisitions):
            reason, detail = exclusion_reason(acq, resolved=resolved, refusal=refusal)
            if reason:
                excluded.append(
                    {
                        "file": path.name,
                        "event_index": i,
                        "model_raw": report.model_name,
                        "acquisition_type": acq.acquisition_type,
                        "reason": reason,
                        "detail": detail,
                    }
                )
                continue
            assert resolved is not None  # exclusion_reason guarantees this

            try:
                cases.append(
                    evaluate_event(
                        file_name=path.name,
                        vendor=resolved[0],
                        model=resolved[1],
                        manufacturer_raw=report.manufacturer,
                        model_raw=report.model_name,
                        index=i,
                        acq=acq,
                    )
                )
            except CoefficientNotAvailable as exc:
                excluded.append(
                    {
                        "file": path.name,
                        "event_index": i,
                        "model_raw": report.model_name,
                        "acquisition_type": acq.acquisition_type,
                        "reason": "technique outside the tabulated grid",
                        "detail": str(exc),
                    }
                )

    reasons: dict[str, int] = {}
    for e in excluded:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1

    errors = [c["percent_error"] for c in cases]
    return {
        "provenance": {
            "generated_by": "tools/evaluate_ge_rdsr.py",
            "question": (
                "Does the open-table reconstruction predict GE CTDIvol? The archive "
                "cannot answer this because GE records CTDIvol in no sampled image "
                "header, so the reference is taken from the scanner's own Radiation "
                "Dose SR instead."
            ),
            "what_this_tests": (
                "the dose model (table coefficient, mAs per rotation, pitch) on GE "
                "scanners. It does NOT test the metadata-gap recovery path, because the "
                "technique is read from a structured report rather than reconstructed "
                "from a de-identified image header."
            ),
            "independence": (
                "Events are not independent patients. The comparison draws several "
                "events from each study, and events within a study share a scanner, a "
                "calibration and a session."
            ),
            "coefficients": provenance(),
            "sample_source": (
                "OpenREM test objects (GPL-3.0), openrem/remapp/tests/test_files/CT-*.dcm, "
                "and a PixelMed sample. The objects are third-party and are not "
                "redistributed here; this file holds only measurements read out of them."
            ),
            "sample_directory": str(sample_dir),
            "eligibility_rules": [
                "the event records a CTDIvol (DCM 113830)",
                "the scanner model resolves in the open coefficient database, exactly "
                "or by an alias denoting the same machine",
                "the acquisition has table feed (helical), because the formula models "
                "one rotation's output spread along the feed",
                "tube potential, mean tube current, exposure time per rotation, pitch, "
                "total collimation and the CTDIw phantom code are all recorded",
            ],
        },
        "formula_input_check": check_formula_inputs(),
        "summary": {
            "n_files": len(files),
            "n_events_seen": sum(f["n_events"] for f in files),
            "n_events_with_ctdivol": sum(f["n_events_with_ctdivol"] for f in files),
            "n_evaluated": len(cases),
            "n_excluded": len(excluded),
            "exclusions_by_reason": reasons,
            "by_model": {
                m: describe([c["percent_error"] for c in cases if c["table_model"] == m])
                for m in sorted({c["table_model"] for c in cases})
            },
            "n_exact_table_match": sum(1 for c in cases if c["exact_table_match"]),
            "overall": describe(errors),
            "implied_coefficient_mgy_per_100mas": describe(
                [c["implied_nctdi_w_mgy_per_100mas"] for c in cases]
            ),
            "collimation_sensitivity": collimation_sensitivity(cases),
        },
        "files": files,
        "cases": cases,
        "excluded": excluded,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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

    payload = run(Path(args.samples))
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "ge_rdsr_accuracy.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    s = payload["summary"]
    check = payload["formula_input_check"]
    print(f"GE files                      : {s['n_files']}")
    print(f"irradiation events            : {s['n_events_seen']}")
    print(f"events recording a CTDIvol    : {s['n_events_with_ctdivol']}")
    print(f"events compared               : {s['n_evaluated']}")
    print(f"formula ignores invented input: {check['invariant']}")
    print("\nexclusions:")
    for reason, n in sorted(s["exclusions_by_reason"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {reason}")
    print("\nper event:")
    for c in payload["cases"]:
        print(
            f"  {c['file'][:22]:24} #{c['event_index']:<3} {c['table_model']:16} "
            f"{c['phantom_cm']:>2}cm  recorded {c['recorded_ctdivol_mgy']:7.2f}  "
            f"estimated {c['estimated_ctdivol_mgy']:7.2f}  "
            f"{c['percent_error']:+8.1f}%   coll {c['recorded_collimation_mm']:g}->"
            f"{c['table_collimation_mm']:g} mm"
        )
    o = s["overall"]
    if o.get("n"):
        print(
            f"\noverall n={o['n']}  median {o['median']:+.1f}%  "
            f"mean {o['mean']:+.1f}%  range {o['min']:+.1f}..{o['max']:+.1f}%  "
            f"within20 {o['within_20_percent']:.0%}"
        )
    imp = s["implied_coefficient_mgy_per_100mas"]
    if imp.get("n"):
        print(
            f"\ncoefficient implied by the recorded CTDIvol: "
            f"{imp['min']:.2f}..{imp['max']:.2f} mGy/100mAs "
            f"(median {imp['median']:.2f}) across {imp['n']} events, "
            f"{len(s['by_model'])} models and both pitches"
        )
    print("\nis the recorded total collimation a plausible beam width?")
    for c in payload["cases"]:
        at = c["beam_width_consistency"]["at_collimation_mm"]
        recorded = next(k for k, v in at.items() if v["is_the_recorded_value"])
        print(
            f"  {c['file'][:22]:24} #{c['event_index']:<3} "
            f"L={c['beam_width_consistency']['scanning_length_mm']:.0f} mm  "
            + "  ".join(
                f"{k:>2} mm: {v['scan_duration_s']:6.1f} s"
                + ("*" if v["is_the_recorded_value"] else " ")
                for k, v in at.items()
            )
            + f"   (* = recorded, {recorded} mm)"
        )
    print("\ncollimation sensitivity (what each tabulated row would give):")
    for coll, d in s["collimation_sensitivity"]["by_collimation_mm"].items():
        print(
            f"  {coll:>6}  median {d['median']:+7.1f}%  "
            f"range {d['min']:+.1f}..{d['max']:+.1f}%  "
            f"mean|err| {d['mean_absolute']:.1f}%"
        )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
