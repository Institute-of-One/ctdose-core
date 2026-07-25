"""Build the small public test fixture from the local DOSE TARO phantom series.

``DOSE TARO`` is a dummy patient record (no PHI) acquired on a GE LightSpeed VCT. The
full series is 147 slices / ~77 MB, which is too large to ship. This script writes:

* ``data/DOSE_TARO/`` -- slices sampled at a fixed stride across the whole scan, with
  identifying and institution attributes stripped and UIDs regenerated, for image-based
  tests (body contour, water-equivalent diameter, SSDE). Striding rather than taking a
  contiguous block keeps the real scan range and, importantly, the tube-current
  modulation, which a handful of adjacent slices would not show;
* ``tests/data/dose_taro_acquisition.json`` -- the acquisition parameters of the
  *full* series, including the complete per-slice tube-current profile, so the dose
  metric regressions exercise realistic full-scan numbers without the pixel data.

Usage::

    python tools/make_fixture.py --source D:/DevGit/CT-ExposureDose/data
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pydicom
from pydicom.uid import generate_uid

from ctdose_core.io import load_series

#: Attributes removed or overwritten before the fixture is published.
SCRUB_BLANK = [
    (0x0008, 0x0050),  # Accession Number
    (0x0008, 0x0080),  # Institution Name
    (0x0008, 0x0081),  # Institution Address
    (0x0008, 0x1010),  # Station Name
    (0x0008, 0x0090),  # Referring Physician's Name
    (0x0008, 0x1060),  # Name of Physician(s) Reading Study
    (0x0008, 0x1070),  # Operators' Name
    (0x0010, 0x0030),  # Patient's Birth Date
    (0x0010, 0x21B0),  # Additional Patient History
    (0x0020, 0x0010),  # Study ID
    (0x0032, 0x1060),  # Requested Procedure Description
]
SCRUB_REPLACE = {
    (0x0010, 0x0010): "DOSE^TARO",  # already a dummy name; normalised
    (0x0010, 0x0020): "CTDOSE-FIXTURE",
    (0x0008, 0x0020): "20000101",  # Study Date
    (0x0008, 0x0021): "20000101",  # Series Date
    (0x0008, 0x0022): "20000101",  # Acquisition Date
    (0x0008, 0x0023): "20000101",  # Content Date
    (0x0008, 0x0012): "20000101",  # Instance Creation Date
    (0x0040, 0x0244): "20000101",  # Performed Procedure Step Start Date
}
#: Sequences that carry copies of the identifiers above.
SCRUB_SEQUENCES = [(0x0040, 0x0275), (0x0008, 0x1110), (0x0008, 0x1140)]


def scrub(ds: pydicom.Dataset, study_uid: str, series_uid: str) -> pydicom.Dataset:
    for tag in SCRUB_BLANK:
        if tag in ds:
            ds[tag].value = ""
    for tag, value in SCRUB_REPLACE.items():
        if tag in ds:
            ds[tag].value = value
    for tag in SCRUB_SEQUENCES:
        if tag in ds:
            del ds[tag]
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    if (0x0020, 0x0052) in ds:  # Frame of Reference UID
        ds[(0x0020, 0x0052)].value = study_uid
    return ds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[1]
    ap.add_argument("--source", type=Path, default=Path(r"D:/DevGit/CT-ExposureDose/data"))
    ap.add_argument("--out-images", type=Path, default=repo / "data" / "DOSE_TARO")
    ap.add_argument("--out-params", type=Path, default=repo / "tests" / "data")
    ap.add_argument("--n-slices", type=int, default=12, help="slices to keep, evenly strided")
    args = ap.parse_args()

    full = load_series(args.source)
    print(f"source series: {full.n_slices} slices, {full.model_name}")

    params = {
        "description": (
            "Acquisition parameters of the full DOSE TARO series (GE LightSpeed VCT, "
            "dummy patient, no PHI). Pixel data is not included; see data/DOSE_TARO for "
            "the image fixture."
        ),
        "n_slices": full.n_slices,
        "manufacturer": full.manufacturer,
        "model_name": full.model_name,
        "protocol_name": full.protocol_name,
        "kvp": full.kvp,
        "tube_current_ma": [float(x) for x in full.tube_current_ma],
        "rotation_time_s": full.rotation_time_s.value,
        "rotation_time_source": full.rotation_time_s.tag,
        "pitch": full.pitch.value,
        "pitch_source": full.pitch.tag,
        "collimation_mm": full.collimation_mm.value,
        "collimation_source": full.collimation_mm.note or full.collimation_mm.tag,
        "table_feed_mm": full.table_feed_mm.value,
        "slice_thickness_mm": full.slice_thickness_mm,
        "slice_locations_mm": [float(x) for x in full.slice_locations_mm],
        "scan_range_mm": full.scan_range_mm,
        "pixel_spacing_mm": list(full.pixel_spacing_mm) if full.pixel_spacing_mm else None,
        "patient_sex": full.patient_sex,
        "patient_age_years": full.patient_age_years,
        "patient_weight_kg": full.patient_weight_kg,
        "ctdivol_tag_present": bool(full.ctdivol_tag and full.ctdivol_tag.value),
        "dlp_record_present": bool(full.dlp_tag and full.dlp_tag.value),
    }
    args.out_params.mkdir(parents=True, exist_ok=True)
    (args.out_params / "dose_taro_acquisition.json").write_text(
        json.dumps(params, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out_params / 'dose_taro_acquisition.json'}")

    stride = max(1, full.n_slices // args.n_slices)
    chosen = full.files[::stride][: args.n_slices]

    args.out_images.mkdir(parents=True, exist_ok=True)
    for old in args.out_images.glob("*.dcm"):
        old.unlink()
    study_uid, series_uid = generate_uid(), generate_uid()
    total = 0
    for i, path in enumerate(chosen, start=1):
        ds = pydicom.dcmread(str(path))
        ds = scrub(ds, study_uid, series_uid)
        ds.InstanceNumber = i
        dest = args.out_images / f"slice_{i:03d}.dcm"
        ds.save_as(str(dest), enforce_file_format=True)
        total += dest.stat().st_size
    print(f"wrote {len(chosen)} slices to {args.out_images} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
