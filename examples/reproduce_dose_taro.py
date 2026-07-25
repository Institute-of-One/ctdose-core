"""End-to-end reproduction on the bundled fixture.

Run from the repository root::

    python examples/reproduce_dose_taro.py

Demonstrates the case the library exists for: a series with a complete acquisition
description and a per-slice tube-current profile, but no recorded CTDIvol and no dose
report. Everything printed carries its provenance.

To run the same analysis on a public TCIA series instead, point ``SERIES`` at the
extracted directory and set ``REGION`` / ``SCANNER`` accordingly.
"""

from __future__ import annotations

from pathlib import Path

from ctdose_core import analyze_series, load_series, size_band
from ctdose_core.drl import DISCLAIMER
from ctdose_core.metrics import fallback_error
from ctdose_core.tcia import inventory

REPO = Path(__file__).resolve().parents[1]
SERIES = REPO / "data" / "DOSE_TARO"
REGION = "Chest"
SCANNER = "GE LightSpeed VCT (small BS)"
DRL_KEY = "routine_chest"


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> None:
    rule("1. What does this series actually carry?")
    series = load_series(SERIES)
    inv = inventory(series)
    print(f"  scanner            : {inv.manufacturer} {inv.model_name}")
    print(f"  protocol           : {series.protocol_name or '(none)'}")
    print(f"  slices             : {inv.n_slices}")
    print(f"  kVp                : {inv.kvp}")
    print(f"  CTDIvol (0018,9345): {'present' if inv.has_ctdivol_tag else 'ABSENT'}")
    print(f"  DLP record         : {'present' if inv.has_dlp_record else 'ABSENT'}")
    print(f"  tube current       : {'present' if inv.has_tube_current else 'absent'}"
          f"{', modulated' if inv.tube_current_is_modulated else ''}")
    for source in (series.pitch, series.collimation_mm, series.rotation_time_s):
        print(f"  {source.value!r:>10} from {source.tag or 'derivation'} {source.note}")

    rule("2. Reconstructed dose indices")
    report = analyze_series(SERIES, region=REGION, scanner_model=SCANNER, drl_key=DRL_KEY)
    for name in ("ctdivol", "dlp", "dlp_air"):
        q = report.quantities[name]
        print(f"  {name:<8} = {q.value:10.4g} {q.unit:<7} [{q.source.value}]")
        print(f"           {q.formula}")

    rule("3. Patient size and SSDE")
    dw = report.quantities["water_equivalent_diameter"]
    print(f"  D_water  = {dw.value:.2f} cm   ({dw.formula})")
    print(f"  f({dw.value:.1f}) = {report.ssde_result.factor.value:.3f} "
          f"({report.ssde_result.phantom_cm} cm phantom)")
    print(f"  SSDE     = {report.ssde.value:.3f} mGy "
          f"(CTDIvol {report.ctdivol.value:.3f} mGy corrected for patient size)")

    rule("4. Effective dose")
    e = report.effective_dose
    print(f"  E = {e.value:.4g} mSv")
    print(f"      {e.formula}")
    print("      population estimate for a reference phantom, not this patient's dose")

    rule("5. DRL comparison")
    for c in report.drl_comparisons:
        verdict = "EXCEEDS" if c.exceeds else "within"
        print(f"  {c.metric:<8} {c.measured:9.4g} vs {c.reference:g} {c.unit:<7} "
              f"-> {c.percent_of_drl:5.0f}% of DRL, {verdict}")
    print(f"  patient size band: {size_band(dw.value)}")
    print(f"  {DISCLAIMER}")

    rule("6. Alerts (auditable)")
    if not report.alerts:
        print("  none")
    for a in report.alerts:
        print(f"  [{a.severity.value}] {a.message}")

    rule("7. Warnings")
    for w in report.warnings or ["none"]:
        print(f"  {w}")

    rule("8. Validating the fallback where a recorded value exists")
    print("  This series has no recorded CTDIvol, so there is nothing to compare against.")
    print("  On a series that does carry the tag, the procedure is:")
    print("      recorded  = analyze_series(path, region=...).ctdivol")
    print("      estimated = analyze_series(path, region=..., prefer_recorded=False).ctdivol")
    print("      fallback_error(float(recorded), float(estimated))")
    demo = fallback_error(recorded=10.0, estimated=9.1)
    print(f"  worked example: {demo['percent_error']:+.1f}% "
          f"(recorded {demo['recorded']}, estimated {demo['estimated']})")


if __name__ == "__main__":
    main()
