"""Command-line interface: ``ctdose <command>``.

Commands
--------
``analyze``    run the full dose analysis on a series and print or write JSON
``inspect``    show which dose-relevant DICOM attributes a series carries
``survey``     inventory every series under a directory (tag coverage)
``models``     list the scanner models in the coefficient table
``drl``        list the shipped diagnostic reference levels
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .alert import summarise
from .io import load_series
from .pipeline import analyze_series
from .tables import conversion_regions, scanner_models
from .tcia import survey_directory, tag_coverage


def _write(payload: dict, out: Path | None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)


def _cmd_analyze(args: argparse.Namespace) -> int:
    report = analyze_series(
        args.path,
        region=args.region,
        scanner_model=args.scanner_model,
        bowtie=args.bowtie,
        patient_size=args.patient_size,
        drl_key=args.drl_key,
        measure_size=not args.no_size,
        prefer_recorded=not args.force_fallback,
        series_uid=args.series_uid,
        use_rdsr=not args.no_rdsr,
        rdsr_path=args.rdsr,
    )
    if args.json or args.out:
        _write(report.to_dict(), Path(args.out) if args.out else None)
        return 0

    q = report.quantities
    print(f"series          : {report.series.n_slices} slices, {report.series.model_name}")
    print(f"scanner model   : {report.scanner_model}")
    print(f"region / size   : {report.region} / {report.patient_size}")
    if report.rdsr is not None:
        name = report.rdsr.path.name if report.rdsr.path else "(in memory)"
        m = report.rdsr_match
        print(
            f"dose SR         : {name}, {len(report.rdsr.acquisitions)} event(s); "
            f"match: {m.method} ({m.confidence})"
        )
    for name, cmp in report.source_agreement.items():
        print(
            f"source check    : {name} {cmp['a']['source']} {cmp['a']['value']:.4g} vs "
            f"{cmp['b']['source']} {cmp['b']['value']:.4g} "
            f"({cmp['percent_difference']:+.1f}%)"
        )
    print(f"CTDIvol         : {float(q['ctdivol']):.3g} mGy   [{q['ctdivol'].source.value}]")
    print(f"DLP             : {float(q['dlp']):.4g} mGy*cm  [{q['dlp'].source.value}]")
    if "water_equivalent_diameter" in q:
        print(f"D_water         : {float(q['water_equivalent_diameter']):.3g} cm")
    if report.ssde_result:
        print(
            f"SSDE            : {float(report.ssde_result.ssde):.3g} mGy "
            f"(f={float(report.ssde_result.factor):.3g}, "
            f"{report.ssde_result.phantom_cm} cm phantom)"
        )
    if report.effective_dose is not None:
        print(f"Effective dose  : {float(report.effective_dose):.3g} mSv")
    for c in report.drl_comparisons:
        flag = "EXCEEDS" if c.exceeds else "within"
        print(
            f"DRL {c.metric:<8}: {c.measured:.4g} vs {c.reference:.4g} {c.unit} "
            f"({c.percent_of_drl:.0f}% of DRL, {flag})"
        )
    if report.alerts:
        print("\nalerts:")
        for a in report.alerts:
            print(f"  [{a.severity.value:<7}] {a.message}")
        print(f"  summary: {summarise(report.alerts)}")
    for w in report.warnings:
        print(f"  ! {w}", file=sys.stderr)
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    series = load_series(args.path, series_uid=args.series_uid)
    _write(series.summary(), Path(args.out) if args.out else None)
    return 0


def _cmd_survey(args: argparse.Namespace) -> int:
    if args.tcia:
        from .survey import VENDOR_QUERIES, run_survey

        result = run_survey(
            vendors=args.vendor or list(VENDOR_QUERIES),
            screen_per_vendor=args.screen_per_vendor,
            fallback_per_vendor=args.fallback_per_vendor,
            seed=args.seed,
            work_dir=args.work_dir,
        )
        _write(result.to_dict(), Path(args.out) if args.out else None)
        return 0
    if not args.path:
        print("give a directory to survey, or --tcia to survey the public archive")
        return 2
    items = survey_directory(args.path, limit=args.limit)
    _write(
        {"coverage": tag_coverage(items), "series": [i.to_dict() for i in items]},
        Path(args.out) if args.out else None,
    )
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    if args.regions:
        for r in conversion_regions():
            print(r)
    else:
        for m in scanner_models():
            print(m)
    return 0


def _cmd_drl(args: argparse.Namespace) -> int:
    from .drl import entries

    for e in entries():
        band = f" [{e.age_band}]" if e.age_band else ""
        print(
            f"{e.key:<20} {e.examination}{band}: CTDIvol {e.ctdivol_mgy:g} mGy, "
            f"DLP {e.dlp_mgycm:g} mGy*cm ({e.phantom_cm} cm phantom, {e.set_name})"
        )
        for note in e.notes:
            print(f"    note: {note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctdose", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"ctdose-core {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="run the full dose analysis on a CT series")
    a.add_argument("path", help="series directory or DICOM file")
    a.add_argument("--region", required=True, help="body region, e.g. Chest (see `ctdose models --regions`)")
    a.add_argument("--scanner-model", help="coefficient-table model name (inferred if omitted)")
    a.add_argument("--bowtie", choices=("small", "medium", "large"), help="scan field of view / bowtie")
    a.add_argument("--patient-size", help="override the age-derived patient-size label")
    a.add_argument("--drl-key", help="DRL examination key, e.g. routine_chest")
    a.add_argument("--series-uid", help="restrict to one Series Instance UID")
    a.add_argument("--rdsr", help="explicit CT Radiation Dose SR file or directory")
    a.add_argument(
        "--no-rdsr", action="store_true", help="ignore any CT Radiation Dose SR beside the series"
    )
    a.add_argument("--no-size", action="store_true", help="skip pixel data / SSDE")
    a.add_argument(
        "--force-fallback",
        action="store_true",
        help="ignore recorded CTDIvol/DLP and always use the physics fallback",
    )
    a.add_argument("--json", action="store_true", help="print the full report as JSON")
    a.add_argument("--out", help="write the JSON report to this path")
    a.set_defaults(func=_cmd_analyze)

    i = sub.add_parser("inspect", help="show the dose-relevant attributes of a series")
    i.add_argument("path")
    i.add_argument("--series-uid")
    i.add_argument("--out")
    i.set_defaults(func=_cmd_inspect)

    s = sub.add_parser(
        "survey",
        help="inventory a local directory, or survey the public archive with --tcia",
    )
    s.add_argument("path", nargs="?", help="directory of series (omit when using --tcia)")
    s.add_argument("--limit", type=int, help="stop after this many local series")
    s.add_argument(
        "--tcia",
        action="store_true",
        help="survey TCIA by manufacturer: recorded-dose absence and fallback error",
    )
    s.add_argument("--vendor", action="append", help="restrict to a vendor (repeatable)")
    s.add_argument("--screen-per-vendor", type=int, default=60)
    s.add_argument("--fallback-per-vendor", type=int, default=8)
    s.add_argument("--seed", type=int, default=20260726)
    s.add_argument("--work-dir", default=".tcia_work", help="download cache directory")
    s.add_argument("--out")
    s.set_defaults(func=_cmd_survey)

    m = sub.add_parser("models", help="list scanner models (or body regions)")
    m.add_argument("--regions", action="store_true", help="list body regions instead")
    m.set_defaults(func=_cmd_models)

    d = sub.add_parser("drl", help="list the shipped diagnostic reference levels")
    d.set_defaults(func=_cmd_drl)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
