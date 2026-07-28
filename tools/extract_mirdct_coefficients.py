"""Extract the open CTDI coefficient table from the MIRDct supplementary annex.

The per-scanner CTDI coefficients this package uses come from the supplementary data of
Dinwiddie et al., *J Appl Clin Med Phys* 2026;27(2):e70473 (doi:10.1002/acm2.70473),
published under CC BY 4.0. That licence is the point: the previous coefficients were
machine-extracted from an in-house MATLAB implementation following the CT-Expo
methodology, and while the *formulae* are free to reimplement, per-scanner *values*
transcribed from a commercial database are a licensing exposure that a permanent archived
release must not carry.

The annex tabulates **CTDIw**, not CTDIvol, at pitch 1.0 and 100 mAs. At pitch 1 the two
coincide, which is why the paper's title says normalized CTDIvol; the distinction matters
downstream, because CTDIw is pitch-independent. The stored unit therefore names CTDIw
explicitly and the consuming code divides by pitch. See
``data/reference/mirdct_dinwiddie2026/SOURCE.md``.

Usage::

    python tools/extract_mirdct_coefficients.py

Reads the annex from ``data/reference/mirdct_dinwiddie2026/`` and writes
``ctdose_core/data/scanner_ctdi_mirdct.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ANNEX_DIR = REPO / "data" / "reference" / "mirdct_dinwiddie2026"
ANNEX = ANNEX_DIR / "ACM2-27-e70473-s001.xlsx"
OUT = REPO / "ctdose_core" / "data" / "scanner_ctdi_mirdct.json"

SHEET = "MIRDct"

CITATION = (
    "Dinwiddie LE, Baggett JM, Kofler JM, et al. Survey of normalized CTDIvol values "
    "across four major computed tomography vendors for use in the MIRDct software. "
    "J Appl Clin Med Phys. 2026;27(2):e70473. doi:10.1002/acm2.70473"
)

#: Columns as the annex heads them, in order. Asserted rather than assumed: a silently
#: reordered or renamed column would produce a plausible table of wrong numbers.
EXPECTED_HEADER = [
    "Manufacturer",
    "Scanner Name",
    "Scan Protocol",
    "Bowtie filter",
    "Phantom",
    "Collimation",
    "kVp",
    "Pitch",
    "mAs",
    "CTDIw",
]

#: The annex normalises every row this way; anything else means the file changed.
NORMALISATION_PITCH = 1
NORMALISATION_MAS = 100


def parse_phantom(value: str) -> int:
    """'16cm' / '32cm' -> 16 / 32."""
    digits = "".join(c for c in str(value) if c.isdigit())
    if digits not in {"16", "32"}:
        raise SystemExit(f"unexpected phantom label {value!r}; expected 16cm or 32cm")
    return int(digits)


def load_rows(path: Path) -> list[dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        raise SystemExit(f"{path.name} has no {SHEET!r} sheet; found {wb.sheetnames}")
    raw = list(wb[SHEET].iter_rows(values_only=True))

    header = [h for h in raw[0] if h is not None]
    if header != EXPECTED_HEADER:
        raise SystemExit(
            f"the {SHEET!r} sheet's columns are not what this extractor was written "
            f"against.\n  expected: {EXPECTED_HEADER}\n  found:    {header}\n"
            "Re-read the annex before trusting any extraction."
        )

    out: list[dict[str, Any]] = []
    for n, row in enumerate(raw[1:], start=2):
        if row[0] is None:
            continue
        record = dict(zip(EXPECTED_HEADER, row, strict=False))
        if record["Pitch"] != NORMALISATION_PITCH or record["mAs"] != NORMALISATION_MAS:
            raise SystemExit(
                f"row {n} is normalised at pitch={record['Pitch']}, mAs={record['mAs']}, "
                f"not pitch={NORMALISATION_PITCH}/{NORMALISATION_MAS} mAs. The stored "
                "unit and the consuming formula both assume the published normalisation."
            )
        if record["CTDIw"] is None or float(record["CTDIw"]) <= 0:
            raise SystemExit(f"row {n} has a non-positive CTDIw: {record['CTDIw']!r}")
        out.append(
            {
                "vendor": str(record["Manufacturer"]).strip(),
                # Verbatim from the annex, typos included, so a reader comparing against
                # the published table finds the same strings. Matching is the reader's
                # problem to solve in scanners.py, not something to fix silently here.
                "model": str(record["Scanner Name"]).strip(),
                "bowtie": str(record["Bowtie filter"]).strip().lower(),
                "phantom_cm": parse_phantom(record["Phantom"]),
                "collimation_mm": float(record["Collimation"]),
                "kvp": float(record["kVp"]),
                "nctdi_w_mgy_per_100mas": float(record["CTDIw"]),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--annex", type=Path, default=ANNEX)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if not args.annex.is_file():
        raise SystemExit(
            f"annex not found: {args.annex}\n"
            "Download the Supporting Information of doi:10.1002/acm2.70473 (CC BY 4.0); "
            "see data/reference/mirdct_dinwiddie2026/SOURCE.md."
        )

    rows = load_rows(args.annex)
    digest = hashlib.sha256(args.annex.read_bytes()).hexdigest()

    vendors = sorted({r["vendor"] for r in rows})
    models = sorted({(r["vendor"], r["model"]) for r in rows})
    payload = {
        "provenance": {
            "name": "MIRDct normalized CTDI database",
            "citation": CITATION,
            "doi": "10.1002/acm2.70473",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "source_file": args.annex.name,
            "source_sheet": SHEET,
            "source_sha256": digest,
            "extracted_by": "tools/extract_mirdct_coefficients.py",
            "quantity": "CTDIw",
            "unit": "mGy per 100 mAs at pitch 1.0",
            "usage": (
                "CTDIvol = nctdi_w_mgy_per_100mas * (mAs_rot / 100) / pitch. The annex "
                "tabulates CTDIw at pitch 1.0, where CTDIw and CTDIvol coincide; CTDIw is "
                "pitch-independent, so the pitch division is applied by the caller."
            ),
            "collimation_note": (
                "Collimation is tabulated directly, so no over-beaming correction is "
                "applied to these values -- the collimation dependence is measured."
            ),
            "model_name_note": (
                "Model names are verbatim from the annex, including the source spellings "
                "'Aquiliion ONE' and 'Defintion Edge'."
            ),
        },
        "summary": {
            "n_rows": len(rows),
            "n_models": len(models),
            "vendors": vendors,
            "models_by_vendor": {
                v: sorted(m for ven, m in models if ven == v) for v in vendors
            },
            "kvp_values": sorted({r["kvp"] for r in rows}),
            "collimation_values_mm": sorted({r["collimation_mm"] for r in rows}),
            "phantoms_cm": sorted({r["phantom_cm"] for r in rows}),
            "bowties": sorted({r["bowtie"] for r in rows}),
        },
        "rows": rows,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"{len(rows)} rows, {len(models)} models across {len(vendors)} vendors")
    for v in vendors:
        ms = payload["summary"]["models_by_vendor"][v]
        print(f"  {v:10s} {len(ms)}: {', '.join(ms)}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
