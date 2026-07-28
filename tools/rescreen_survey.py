"""Re-screen the surveyed series with the current engine, without re-sampling the archive.

The screen records two kinds of thing. Most of it -- which attributes a header carries --
depends only on the DICOM and cannot change when the engine does. Two columns do depend on
the engine: whether the scanner resolves to a coefficient row, and whether the series is
reconstructable at all. Replacing the coefficient table changes those, and therefore
changes the conditional reconstructability the paper's central claim rests on.

Re-running ``run_tcia_survey.py`` would also draw a fresh sample from an archive that has
moved on, so two causes would be mixed and neither attributable. This script instead
re-screens **exactly the series the reported run screened**, identified by Series Instance
UID, from the cached headers. Same sample, same recorded values, new engine.

Usage::

    python tools/rescreen_survey.py
    python tools/rescreen_survey.py --check    # report the deltas, write nothing

Writes ``results/survey.json`` and ``results/survey.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from ctdose_core.scanners import AmbiguousScannerError, resolve_model
from ctdose_core.survey import SeriesScreen, summarise_screens

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"

DERIVED = ("fallback_feasible", "surveyable")

SCREEN_COLUMNS = [
    "vendor", "collection", "model_name", "model_resolution", "model_resolved",
    "n_images", "has_ctdivol_tag", "has_dlp_record", "has_tube_current", "has_timing",
    "has_collimation", "has_pitch", "has_slice_thickness", "has_slice_location",
    "fallback_feasible", "surveyable", "licence", "series_uid", "error",
]


def rescreen(row: dict[str, Any]) -> SeriesScreen:
    """Rebuild one screen, recomputing only what the engine decides."""
    screen = SeriesScreen(**{k: v for k, v in row.items() if k not in DERIVED})
    if screen.error:
        return screen
    try:
        model, _ = resolve_model(screen.model_name, screen.manufacturer_raw)
        screen.model_resolved, screen.model_resolution = model, "resolved"
    except AmbiguousScannerError as exc:  # pragma: no cover - exact matching removes this
        screen.model_resolution = "ambiguous"
        screen.model_resolved = "|".join(c.table_model for c in exc.candidates)
    except LookupError:
        screen.model_resolution = "unknown"
        screen.model_resolved = None
    return screen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", type=Path, default=RESULTS / "survey.json")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    payload = json.loads(args.survey.read_text(encoding="utf-8"))
    before = payload["tag_absence"]
    screens = [rescreen(r) for r in payload["series"]]
    after = summarise_screens(screens)

    def line(label: str, a: Any, b: Any) -> str:
        return f"  {label:34s} {a!s:>18s} -> {b!s:>18s}"

    print("conditional reconstructability, by manufacturer")
    for vendor in sorted(after["by_vendor"]):
        b0, b1 = before["by_vendor"][vendor], after["by_vendor"][vendor]
        print(line(
            vendor,
            f"{b0['n_without_ctdivol_reconstructable']} ({_pct(b0['conditional_reconstructability_rate'])})",
            f"{b1['n_without_ctdivol_reconstructable']} ({_pct(b1['conditional_reconstructability_rate'])})",
        ))
    o0, o1 = before["overall"], after["overall"]
    print(line("ALL", f"{o0['n_without_ctdivol_reconstructable']}", f"{o1['n_without_ctdivol_reconstructable']}"))
    print()
    print(line("surveyable (either route)", o0["surveyable"], o1["surveyable"]))
    print(line("model names resolved", before["overall"]["model_resolution"].get("resolved", 0),
               after["overall"]["model_resolution"].get("resolved", 0)))
    print(line("model names not covered", before["overall"]["model_resolution"].get("unknown", 0),
               after["overall"]["model_resolution"].get("unknown", 0)))
    print(line("CTDIvol absent (unchanged by the engine)",
               o0["without_ctdivol_tag"], o1["without_ctdivol_tag"]))

    if args.check:
        print("\n--check: nothing written")
        return 0

    payload["tag_absence"] = after
    payload["series"] = [s.to_dict() for s in screens]
    payload.setdefault("provenance", {})["rescreened_by"] = "tools/rescreen_survey.py"
    payload["provenance"]["rescreened_note"] = (
        "The sample is the archive run recorded in access_started_utc/access_finished_utc; "
        "the screened attributes are unchanged. Scanner-model resolution and "
        "reconstructability were recomputed against the open coefficient database, which "
        "covers a different set of scanners than the table used for the original run."
    )
    args.survey.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    csv_path = args.survey.parent / "survey.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCREEN_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for s in screens:
            w.writerow(s.to_dict())
    print(f"\nwrote {args.survey}\nwrote {csv_path}")
    return 0


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
