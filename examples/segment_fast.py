"""Fast TotalSegmentator organ segmentation with timing (diagnose slow runs).

Usage:
    python examples/segment_fast.py <CT series directory> [--all] [--device gpu|cpu]

Uses fast (3 mm) mode and the compact dose-relevant organ subset by default, which
is far faster than segmenting all 100+ structures. First run downloads the models.
"""

from __future__ import annotations

import sys
import time

from ctdose_core.io import load_series
from ctdose_core.organ import DOSE_ROI_SUBSET, segment_organs, totalsegmentator_available


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("give a CT series directory")
        return 2
    path = args[0]
    use_all = "--all" in args
    hires = "--hires" in args  # full-resolution model (crisp masks, slower)
    device = None
    if "--device" in args:
        device = args[args.index("--device") + 1]
    if not totalsegmentator_available():
        print('TotalSegmentator not installed. Run: pip install "ctdose-core[ai]"')
        return 2

    series = load_series(path)
    print(f"series: {series.manufacturer} {series.model_name}, {len(series.files)} slices")
    print(f"mode: {'hires' if hires else 'fast(3mm)'} + {'ALL organs' if use_all else f'{len(DOSE_ROI_SUBSET)} dose-relevant organs'}"
          f", device={device or 'auto'}")
    t0 = time.time()
    out_dir = None
    if "--out" in args:
        out_dir = args[args.index("--out") + 1]
    masks = segment_organs(
        series,
        output_dir=out_dir,
        fast=not hires,    # --hires -> full resolution (sharper boundaries)
        roi_subset=None if use_all else DOSE_ROI_SUBSET,
        device=device,
        nr_thr_resamp=1,   # keep multiprocessing minimal (Windows-friendly)
        nr_thr_saving=1,
    )
    saved = out_dir or (str(series.files[0].parent / "_ctdose_segmentation"))
    print(f"masks saved to: {saved}  (point the GUI's 'precomputed masks folder' here)")
    dt = time.time() - t0
    print(f"\nsegmented {len(masks)} organs in {dt:.1f} s")
    for name, m in sorted(masks.items(), key=lambda kv: -int(kv[1].sum())):
        n = int(m.sum())
        if n:
            print(f"  {name:26s} {n:>8d} vox")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
