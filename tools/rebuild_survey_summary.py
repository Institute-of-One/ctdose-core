"""Recompute the survey's summary blocks from the per-series rows already in ``results/``.

``run_tcia_survey.py`` needs the network and re-samples the archive, which may have changed
since the reported run. When only the *summary* logic changes -- a new stratum, a corrected
denominator -- the honest thing is to recompute from the rows that were actually screened,
not to re-sample.

This reads ``results/survey.json``, rebuilds the :class:`SeriesScreen` and
:class:`FallbackCase` records it contains, re-runs the aggregation, and writes the file
back with the per-series and per-case rows untouched. The provenance block records that
the summary was rebuilt and from which run.

Usage::

    python tools/rebuild_survey_summary.py
    python tools/rebuild_survey_summary.py --check     # verify only, do not write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ctdose_core.survey import (
    FallbackCase,
    SeriesScreen,
    summarise_fallback,
    summarise_screens,
)

REPO = Path(__file__).resolve().parents[1]
SURVEY = REPO / "results" / "survey.json"

#: Fields the dataclasses derive rather than store; dropped before reconstruction.
DERIVED_SCREEN_FIELDS = ("fallback_feasible", "surveyable")


def screens_from(payload: dict) -> list[SeriesScreen]:
    out = []
    for row in payload["series"]:
        out.append(SeriesScreen(**{k: v for k, v in row.items() if k not in DERIVED_SCREEN_FIELDS}))
    return out


def cases_from(payload: dict) -> list[FallbackCase]:
    return [FallbackCase(**row) for row in payload["fallback_cases"]]


def rebuild(payload: dict) -> dict[str, Any]:
    screens = screens_from(payload)
    cases = cases_from(payload)
    out = dict(payload)
    out["tag_absence"] = summarise_screens(screens)
    out["fallback_error"] = summarise_fallback(cases)
    provenance = dict(out.get("provenance", {}))
    provenance["summary_rebuilt_by"] = "tools/rebuild_survey_summary.py"
    provenance["summary_rebuilt_note"] = (
        "The per-series and per-case rows are from the original archive run recorded in "
        "access_started_utc/access_finished_utc; only the aggregation was recomputed."
    )
    out["provenance"] = provenance
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", type=Path, default=SURVEY)
    ap.add_argument("--check", action="store_true", help="report differences without writing")
    args = ap.parse_args()

    payload = json.loads(args.survey.read_text(encoding="utf-8"))
    rebuilt = rebuild(payload)

    old_a, new_a = payload["tag_absence"]["overall"], rebuilt["tag_absence"]["overall"]
    changed = sorted(set(new_a) - set(old_a)) + [
        k for k in old_a if k in new_a and old_a[k] != new_a[k]
    ]
    print(f"series rows      : {len(payload['series'])} (unchanged)")
    print(f"fallback cases   : {len(payload['fallback_cases'])} (unchanged)")
    print(f"summary keys new/changed: {changed or 'none'}")
    for vendor, block in rebuilt["tag_absence"]["by_vendor"].items():
        print(
            f"  {vendor:15s} without CTDIvol {block['without_ctdivol_tag']:3d}  "
            f"reconstructable {block['n_without_ctdivol_reconstructable']:3d}  "
            f"({100 * (block['conditional_reconstructability_rate'] or 0):.1f}%)"
        )
    o = rebuilt["tag_absence"]["overall"]
    print(
        f"  {'ALL':15s} without CTDIvol {o['without_ctdivol_tag']:3d}  "
        f"reconstructable {o['n_without_ctdivol_reconstructable']:3d}  "
        f"({100 * (o['conditional_reconstructability_rate'] or 0):.1f}%)"
    )

    if args.check:
        print("\n--check: nothing written")
        return 0
    args.survey.write_text(
        json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {args.survey}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
