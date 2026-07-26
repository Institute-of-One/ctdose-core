"""Parse a directory of CT Radiation Dose SR objects and print a per-vendor summary.

Usage:
    CTDOSE_RDSR_SAMPLES=/path/to/CT-RDSR-samples python examples/validate_rdsr_multivendor.py
    # or:
    python examples/validate_rdsr_multivendor.py /path/to/samples

The sample RDSRs (GE / Siemens / Canon-Toshiba / Philips) can be obtained from the
OpenREM test data (GPL-3.0):  pip download openrem --no-deps --no-binary :all:
then extract openrem/remapp/tests/test_files/CT-*.dcm .
"""

from __future__ import annotations

import glob
import os
import sys

from ctdose_core.rdsr import read_rdsr


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CTDOSE_RDSR_SAMPLES", "")
    if not root or not os.path.isdir(root):
        print("set CTDOSE_RDSR_SAMPLES or pass a directory of CT-*.dcm RDSR files")
        return 2
    files = sorted(glob.glob(os.path.join(root, "CT-*.dcm")))
    print(f"{'file':32s} | {'vendor':9s} | {'model':22s} | ev | CTDIvol (mGy) | total DLP | phantom")
    print("-" * 120)
    parsed = 0
    for f in files:
        r = read_rdsr(f)
        name = os.path.basename(f)[:32]
        if r is None:
            print(f"{name:32s} | (dose-screen image, not a CT RDSR)")
            continue
        parsed += 1
        ctdis = ",".join(f"{float(a.ctdivol):.2f}" for a in r.acquisitions if a.ctdivol)
        ph = next((a.phantom_type for a in r.acquisitions if a.phantom_type), "")
        tot = f"{float(r.total_dlp):.0f}" if r.total_dlp else "-"
        vendor = (r.manufacturer or "?").split()[0][:9]
        print(f"{name:32s} | {vendor:9s} | {r.model_name[:22]:22s} | "
              f"{len(r.acquisitions):2d} | {ctdis[:13]:13s} | {tot:9s} | {ph}")
    print("-" * 120)
    print(f"parsed as CT RDSR: {parsed}/{len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
