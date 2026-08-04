"""DICOM ingest: turn a CT series directory into a :class:`ScanSeries`.

Attribute selection and the fallback chains follow the reference implementation,
which was written against real GE, Canon/Toshiba, Siemens and Philips output and
therefore knows which private attributes to reach for when the standard ones are
missing. Every fallback is recorded so the caller can see how a value was obtained.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:  # pragma: no cover - import guard only
    import pydicom
    from pydicom.dataset import Dataset
except ImportError as exc:  # pragma: no cover
    raise ImportError("ctdose-core requires pydicom; install with `pip install ctdose-core`") from exc

from .provenance import Quantity, Source

# --- attribute tags -------------------------------------------------------------------

T_KVP = (0x0018, 0x0060)
T_TUBE_CURRENT = (0x0018, 0x1151)
T_EXPOSURE_TIME = (0x0018, 0x1150)
T_SLICE_THICKNESS = (0x0018, 0x0050)
T_SLICE_LOCATION = (0x0020, 0x1041)
T_IMAGE_POSITION = (0x0020, 0x0032)
T_PIXEL_SPACING = (0x0028, 0x0030)
T_ROWS = (0x0028, 0x0010)
T_COLUMNS = (0x0028, 0x0011)
T_INSTANCE_NUMBER = (0x0020, 0x0013)
T_CTDIVOL = (0x0018, 0x9345)
T_DOSE_COMMENT = (0x0040, 0x0310)
T_REVOLUTION_TIME = (0x0018, 0x9305)
T_SINGLE_COLLIMATION = (0x0018, 0x9306)
T_TOTAL_COLLIMATION = (0x0018, 0x9307)
T_TABLE_FEED_PER_ROT = (0x0018, 0x9310)
T_SPIRAL_PITCH = (0x0018, 0x9311)
T_GE_ROTATION_SPEED = (0x0019, 0x1027)
T_GE_TABLE_SPEED = (0x0019, 0x1023)
T_GE_SCAN_PITCH_RATIO = (0x0043, 0x1027)
T_TOSHIBA_PITCH = (0x7005, 0x1023)
T_TOSHIBA_TABLE_SPEED = (0x7005, 0x100A)

DICOM_EXTENSIONS = (".dcm", ".DCM", ".ima", ".IMA", "")

T_PIXEL_PADDING = (0x0028, 0x0120)
T_PIXEL_REPRESENTATION = (0x0028, 0x0103)

#: A padding rule that would blank at least this fraction of a slice is not describing
#: padding, and is refused. See :func:`_padding_value` for how one comes to exist.
MAX_PADDED_FRACTION = 0.98


@dataclass
class Provenanced:
    """A raw acquisition parameter together with the attribute it came from."""

    value: Any
    source: Source
    tag: str = ""
    note: str = ""

    def __bool__(self) -> bool:
        return self.value is not None


def _tag_str(tag: tuple[int, int]) -> str:
    return f"({tag[0]:04X},{tag[1]:04X})"


def _get(ds: Dataset, tag: tuple[int, int]) -> Any:
    try:
        elem = ds[tag]
    except KeyError:
        return None
    return elem.value


def _as_float(value: Any) -> float | None:
    """Coerce a DICOM value (DS/IS/bytes/str/sequence) to a float where possible."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("ascii", errors="ignore")
        except Exception:  # pragma: no cover - defensive
            return None
    if isinstance(value, str):
        m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)
        return float(m.group()) if m else None
    if isinstance(value, (list, tuple)) or (
        hasattr(value, "__len__") and not isinstance(value, (int, float))
    ):
        seq = list(value)
        return _as_float(seq[0]) if seq else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- per-parameter extraction ---------------------------------------------------------


def _rotation_time_s(ds: Dataset) -> Provenanced:
    """Gantry rotation time in seconds.

    Preference order matches the reference implementation: the GE private
    ``Rotation Speed`` (already seconds), then the standard ``Revolution Time``,
    then ``Exposure Time`` (milliseconds, the Canon/Toshiba route).
    """
    v = _as_float(_get(ds, T_GE_ROTATION_SPEED))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_GE_ROTATION_SPEED), "GE private, seconds")
    v = _as_float(_get(ds, T_REVOLUTION_TIME))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_REVOLUTION_TIME), "Revolution Time, seconds")
    v = _as_float(_get(ds, T_EXPOSURE_TIME))
    if v:
        return Provenanced(v / 1000.0, Source.DICOM_TAG, _tag_str(T_EXPOSURE_TIME), "Exposure Time, ms -> s")
    return Provenanced(None, Source.PHYSICS_FALLBACK, note="rotation time unavailable")


def _pitch(ds: Dataset) -> Provenanced:
    """Helical pitch factor.

    GE encodes it as a ``Scan Pitch Ratio`` string such as ``'0.984:1'`` (and the
    legacy ``HQ``/``HS`` modes); Canon/Toshiba uses a private string; the standard
    attribute is ``Spiral Pitch Factor``. Axial acquisitions default to 1.0.
    """
    raw = _get(ds, T_GE_SCAN_PITCH_RATIO)
    if raw is not None:
        text = raw.decode("ascii", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
        text = text.strip()
        if text == "HQ":
            return Provenanced(0.75, Source.DICOM_TEXT, _tag_str(T_GE_SCAN_PITCH_RATIO), "GE HQ mode")
        if text == "HS":
            return Provenanced(1.5, Source.DICOM_TEXT, _tag_str(T_GE_SCAN_PITCH_RATIO), "GE HS mode")
        v = _as_float(text.replace(":1", ""))
        if v:
            return Provenanced(v, Source.DICOM_TEXT, _tag_str(T_GE_SCAN_PITCH_RATIO), "GE 'p:1' string")
    v = _as_float(_get(ds, T_SPIRAL_PITCH))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_SPIRAL_PITCH))
    v = _as_float(_get(ds, T_TOSHIBA_PITCH))
    if v:
        return Provenanced(v, Source.DICOM_TEXT, _tag_str(T_TOSHIBA_PITCH), "Canon/Toshiba private")
    return Provenanced(1.0, Source.PHYSICS_FALLBACK, note="assumed axial acquisition (pitch = 1.0)")


def _table_feed_per_rotation_mm(ds: Dataset, slice_thickness: float | None) -> Provenanced:
    v = _as_float(_get(ds, T_TABLE_FEED_PER_ROT))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_TABLE_FEED_PER_ROT))
    v = _as_float(_get(ds, T_GE_TABLE_SPEED))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_GE_TABLE_SPEED), "GE private, mm/rotation")
    v = _as_float(_get(ds, T_TOSHIBA_TABLE_SPEED))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_TOSHIBA_TABLE_SPEED), "Canon/Toshiba private")
    if slice_thickness:
        return Provenanced(
            slice_thickness, Source.PHYSICS_FALLBACK, note="assumed axial: feed = slice thickness"
        )
    return Provenanced(None, Source.PHYSICS_FALLBACK, note="table feed unavailable")


def _collimation_mm(ds: Dataset, table_feed: float | None, pitch: float | None) -> Provenanced:
    """Total collimation.

    ``Total Collimation Width`` when present; otherwise reconstructed as
    ``table feed per rotation / pitch``, which is the identity that defines pitch.
    """
    v = _as_float(_get(ds, T_TOTAL_COLLIMATION))
    if v:
        return Provenanced(v, Source.DICOM_TAG, _tag_str(T_TOTAL_COLLIMATION))
    if table_feed and pitch:
        return Provenanced(
            table_feed / pitch,
            Source.PHYSICS_FALLBACK,
            note="h_col = table feed per rotation / pitch",
        )
    return Provenanced(None, Source.PHYSICS_FALLBACK, note="collimation unavailable")


def _dlp_from_comment(ds: Dataset) -> Provenanced:
    """Parse DLP out of ``Comments on Radiation Dose`` (0040,0310).

    GE writes a free-text dose summary here. A bare number is taken as mGy*cm; a
    ``DLP=<n>`` style string is parsed as well.
    """
    raw = _get(ds, T_DOSE_COMMENT)
    if raw is None:
        return Provenanced(None, Source.DICOM_TEXT)
    text = raw.decode("ascii", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
    m = re.search(r"DLP\s*[:=]?\s*([-+]?\d*\.?\d+)", text, re.IGNORECASE)
    if m:
        return Provenanced(float(m.group(1)), Source.DICOM_TEXT, _tag_str(T_DOSE_COMMENT), text.strip())
    v = _as_float(text)
    if v is not None:
        return Provenanced(v, Source.DICOM_TEXT, _tag_str(T_DOSE_COMMENT), text.strip())
    return Provenanced(None, Source.DICOM_TEXT, _tag_str(T_DOSE_COMMENT), text.strip())


def _padding_value(ds: Dataset) -> float | None:
    """Pixel Padding Value (0028,0120), read the way its encoder meant it.

    The value representation of this attribute depends on another attribute: US when
    Pixel Representation is 0, SS when it is 1. Implementations do not reliably honour
    that. GE writes ``63536`` with VR US on signed pixel data, which is the two's
    complement encoding of the ``-2000`` it intended.

    Taken literally, 63536 gives a padding threshold far above any Hounsfield value in
    the image, so *every* voxel is treated as padding and the volume becomes uniform
    air. Nothing downstream can detect that: the body contour finds no patient, the
    water-equivalent diameter has nothing to measure, and the failure looks like an
    empty image rather than a mis-read attribute.
    """
    raw = _get(ds, T_PIXEL_PADDING)
    value = _as_float(raw)
    if value is None:
        return None
    representation = _as_float(_get(ds, T_PIXEL_REPRESENTATION)) or 0.0
    if int(representation) == 1 and value > 32767:
        value -= 65536.0
    return value


def _parse_age(ds: Dataset) -> float | None:
    """Patient age in years from ``(0010,1010)``, falling back to birth/study dates."""
    raw = _get(ds, (0x0010, 0x1010))
    if raw:
        text = str(raw).strip()
        m = re.match(r"(\d+)\s*([DWMY])?", text, re.IGNORECASE)
        if m:
            n = float(m.group(1))
            unit = (m.group(2) or "Y").upper()
            return {"Y": n, "M": n / 12.0, "W": n / 52.0, "D": n / 365.0}[unit]
    birth, study = _get(ds, (0x0010, 0x0030)), _get(ds, (0x0008, 0x0020))
    if birth and study and len(str(birth)) == 8 and len(str(study)) == 8:
        b, s = str(birth), str(study)
        years = int(s[:4]) - int(b[:4])
        if (int(s[4:6]), int(s[6:8])) < (int(b[4:6]), int(b[6:8])):
            years -= 1
        return float(years)
    return None


# --- series container -------------------------------------------------------------------


@dataclass
class ScanSeries:
    """One reconstructed CT series and the acquisition parameters needed for dosimetry.

    Slice-level tube current (``tube_current_ma``) is retained per slice so that tube
    current modulation can be used directly for organ-level weighting.
    """

    files: list[Path]
    manufacturer: str = ""
    model_name: str = ""
    station_name: str = ""
    protocol_name: str = ""
    series_description: str = ""
    study_instance_uid: str = ""
    series_instance_uid: str = ""
    #: Directory the series was loaded from; where a sibling dose SR is looked for.
    source_root: Path | None = None

    kvp: float | None = None
    tube_current_ma: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    rotation_time_s: Provenanced | None = None
    pitch: Provenanced | None = None
    collimation_mm: Provenanced | None = None
    table_feed_mm: Provenanced | None = None
    slice_thickness_mm: float | None = None
    slice_locations_mm: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    pixel_spacing_mm: tuple[float, float] | None = None
    rows: int | None = None
    columns: int | None = None
    #: Scan field of view, from Data Collection Diameter (0018,0090); used to infer the
    #: bowtie / scan-field configuration for scanners whose coefficients depend on it.
    data_collection_diameter_mm: float | None = None
    #: Reconstruction Diameter (0018,1100); a fallback proxy for the scan field of view.
    reconstruction_diameter_mm: float | None = None

    ctdivol_tag: Provenanced | None = None
    dlp_tag: Provenanced | None = None

    patient_sex: str = ""
    patient_age_years: float | None = None
    patient_weight_kg: float | None = None
    patient_size_m: float | None = None

    warnings: list[str] = field(default_factory=list)

    # -- derived acquisition quantities ------------------------------------------------

    @property
    def n_slices(self) -> int:
        return len(self.files)

    @property
    def mean_tube_current_ma(self) -> float:
        if self.tube_current_ma.size == 0:
            raise ValueError("series has no tube-current values")
        return float(np.mean(self.tube_current_ma))

    @property
    def scan_range_mm(self) -> float:
        """Reconstructed longitudinal range, first to last slice location."""
        if self.slice_locations_mm.size < 2:
            return 0.0
        return float(abs(self.slice_locations_mm[0] - self.slice_locations_mm[-1]))

    @property
    def slice_spacing_mm(self) -> float:
        if self.slice_locations_mm.size < 2:
            return float(self.slice_thickness_mm or 1.0)
        return float(np.median(np.abs(np.diff(self.slice_locations_mm))))

    @property
    def mas_per_rotation(self) -> float:
        """Effective mAs per rotation, ``mean mA x rotation time``."""
        if self.rotation_time_s is None or self.rotation_time_s.value is None:
            raise ValueError("rotation time is unavailable; cannot compute mAs")
        return self.mean_tube_current_ma * float(self.rotation_time_s.value)

    @property
    def is_pediatric(self) -> bool:
        return self.patient_age_years is not None and self.patient_age_years < 15

    def tube_current_quantity(self) -> Quantity:
        return Quantity(
            value=self.mean_tube_current_ma,
            unit="mA",
            source=Source.DICOM_TAG,
            formula="mean over slices of X-Ray Tube Current",
            inputs={"tag": _tag_str(T_TUBE_CURRENT), "n_slices": self.n_slices},
        )

    def load_volume(self, dtype: type = np.float32) -> np.ndarray:
        """Read the pixel data as Hounsfield units, shaped ``(n_slices, rows, cols)``.

        Raises:
            ValueError: if the series has no readable images, or if the slices do not all
                share one matrix size. Public archives do contain series whose images were
                reconstructed at different matrices, and silently cropping or padding them
                would corrupt every area-based measurement downstream; the caller is told
                instead.
        """
        vol = None
        expected: tuple[int, ...] | None = None
        for i, path in enumerate(self.files):
            ds = pydicom.dcmread(str(path))
            arr = ds.pixel_array.astype(dtype)
            slope = _as_float(_get(ds, (0x0028, 0x1053))) or 1.0
            intercept = _as_float(_get(ds, (0x0028, 0x1052))) or 0.0
            arr = arr * slope + intercept
            pad = _padding_value(ds)
            if pad is not None:
                padded = arr <= pad * slope + intercept
                # A padding rule that would blank essentially the whole image is not
                # describing padding. No reconstruction pads away the entire field, so
                # the rule is rejected and the slice kept -- otherwise a single
                # mis-encoded attribute turns the volume into uniform air, and the only
                # symptom is that everything measured from it silently becomes nothing.
                if padded.mean() <= MAX_PADDED_FRACTION:
                    arr[padded] = -1000.0
                else:
                    self.warnings.append(
                        f"{path.name}: pixel padding value {pad:g} would blank "
                        f"{padded.mean():.1%} of the slice; the rule was not applied"
                    )
            if vol is None:
                expected = arr.shape
                vol = np.empty((len(self.files), *arr.shape), dtype=dtype)
            elif arr.shape != expected:
                raise ValueError(
                    f"series has inconsistent image dimensions: {path.name} is "
                    f"{arr.shape} but the series starts at {expected}. Split the series "
                    "by matrix size, or select one Series Instance UID."
                )
            vol[i] = arr
        if vol is None:
            raise ValueError("series contains no readable images")
        return vol

    def summary(self) -> dict[str, Any]:
        def p(x: Provenanced | None) -> dict[str, Any] | None:
            return None if x is None else {"value": x.value, "source": x.source.value, "tag": x.tag, "note": x.note}

        return {
            "n_slices": self.n_slices,
            "manufacturer": self.manufacturer,
            "model_name": self.model_name,
            "protocol_name": self.protocol_name,
            "series_description": self.series_description,
            "study_instance_uid": self.study_instance_uid,
            "series_instance_uid": self.series_instance_uid,
            "kvp": self.kvp,
            "mean_tube_current_ma": self.mean_tube_current_ma if self.tube_current_ma.size else None,
            "rotation_time_s": p(self.rotation_time_s),
            "pitch": p(self.pitch),
            "collimation_mm": p(self.collimation_mm),
            "table_feed_mm": p(self.table_feed_mm),
            "slice_thickness_mm": self.slice_thickness_mm,
            "scan_range_mm": self.scan_range_mm,
            "ctdivol_tag": p(self.ctdivol_tag),
            "dlp_tag": p(self.dlp_tag),
            "patient": {
                "sex": self.patient_sex,
                "age_years": self.patient_age_years,
                "weight_kg": self.patient_weight_kg,
                "size_m": self.patient_size_m,
            },
            "warnings": list(self.warnings),
        }


# --- loading ---------------------------------------------------------------------------


def iter_dicom_files(path: Path | str) -> list[Path]:
    """Every readable DICOM file under ``path`` (a directory or a single file)."""
    p = Path(path)
    if p.is_file():
        return [p]
    candidates = sorted(q for q in p.rglob("*") if q.is_file())
    out = []
    for q in candidates:
        try:
            pydicom.dcmread(str(q), stop_before_pixels=True, specific_tags=[])
        except Exception:
            continue
        out.append(q)
    return out


def _sort_key(ds: Dataset) -> tuple[float, float]:
    loc = _as_float(_get(ds, T_SLICE_LOCATION))
    if loc is None:
        pos = _get(ds, T_IMAGE_POSITION)
        if pos is not None and len(pos) >= 3:
            loc = _as_float(pos[2])
    inst = _as_float(_get(ds, T_INSTANCE_NUMBER))
    return (loc if loc is not None else math.inf, inst if inst is not None else math.inf)


def load_series(
    path: Path | str,
    *,
    series_uid: str | None = None,
    files: Sequence[Path] | None = None,
) -> ScanSeries:
    """Load a CT series and extract the acquisition parameters needed for dosimetry.

    Args:
        path: directory containing the series (searched recursively) or a single file.
        series_uid: restrict to one Series Instance UID when the directory holds several.
        files: explicit file list, bypassing the directory scan.

    Returns:
        A :class:`ScanSeries` sorted by slice location.
    """
    paths = list(files) if files is not None else iter_dicom_files(path)
    if not paths:
        raise FileNotFoundError(f"no DICOM files found under {path}")

    headers: list[tuple[Path, Dataset]] = []
    for q in paths:
        try:
            ds = pydicom.dcmread(str(q), stop_before_pixels=True)
        except Exception:
            continue
        if str(_get(ds, (0x0008, 0x0060)) or "").upper() not in ("CT", ""):
            continue
        if series_uid and str(_get(ds, (0x0020, 0x000E)) or "") != series_uid:
            continue
        headers.append((q, ds))
    if not headers:
        raise FileNotFoundError(f"no CT images found under {path}")

    uids = {str(_get(ds, (0x0020, 0x000E)) or "") for _, ds in headers}
    warnings: list[str] = []
    if len(uids) > 1 and series_uid is None:
        biggest = max(uids, key=lambda u: sum(1 for _, ds in headers if str(_get(ds, (0x0020, 0x000E)) or "") == u))
        warnings.append(
            f"{len(uids)} series present; using the largest ({biggest}). "
            "Pass series_uid= to select explicitly."
        )
        headers = [(q, ds) for q, ds in headers if str(_get(ds, (0x0020, 0x000E)) or "") == biggest]

    headers.sort(key=lambda t: _sort_key(t[1]))
    first = headers[0][1]

    locations: list[float] = []
    currents: list[float] = []
    for _, ds in headers:
        loc = _as_float(_get(ds, T_SLICE_LOCATION))
        if loc is None:
            pos = _get(ds, T_IMAGE_POSITION)
            loc = _as_float(pos[2]) if pos is not None and len(pos) >= 3 else None
        if loc is not None:
            locations.append(loc)
        ma = _as_float(_get(ds, T_TUBE_CURRENT))
        if ma is not None:
            currents.append(ma)

    if not currents:
        warnings.append(
            f"no X-Ray Tube Current {_tag_str(T_TUBE_CURRENT)} on any slice; "
            "the physics fallback cannot run"
        )
    elif len(currents) < len(headers):
        warnings.append(
            f"tube current present on {len(currents)}/{len(headers)} slices; "
            "the mean is taken over the available slices"
        )
    if len(set(np.round(currents, 3))) > 1:
        pass  # modulated acquisition: expected, no warning
    if not locations:
        warnings.append("no slice locations; scan range will be 0 and DLP cannot be estimated")
    elif len(headers) > 1 and len(set(locations)) == 1:
        # Seen in public archives: de-identification flattens or removes the z coordinate,
        # leaving every slice reporting the same position. CTDIvol is unaffected, but the
        # scan range collapses to zero and any DLP or effective dose derived from it is
        # meaningless rather than merely imprecise.
        warnings.append(
            f"all {len(locations)} slices report the same longitudinal position "
            f"({locations[0]:g} mm); the scan range is unrecoverable, so DLP and "
            "effective dose must not be derived from this series"
        )

    thickness = _as_float(_get(first, T_SLICE_THICKNESS))
    pitch = _pitch(first)
    feed = _table_feed_per_rotation_mm(first, thickness)
    collim = _collimation_mm(first, feed.value, pitch.value)
    rot = _rotation_time_s(first)

    ctdivol_val = _as_float(_get(first, T_CTDIVOL))
    ctdivol = (
        Provenanced(ctdivol_val, Source.DICOM_TAG, _tag_str(T_CTDIVOL))
        if ctdivol_val
        else Provenanced(None, Source.PHYSICS_FALLBACK, note=f"{_tag_str(T_CTDIVOL)} absent")
    )
    dlp = _dlp_from_comment(first)

    spacing = _get(first, T_PIXEL_SPACING)
    pixel_spacing = None
    if spacing is not None and len(spacing) >= 2:
        pixel_spacing = (float(spacing[0]), float(spacing[1]))

    series = ScanSeries(
        files=[q for q, _ in headers],
        manufacturer=str(_get(first, (0x0008, 0x0070)) or "").strip(),
        model_name=str(_get(first, (0x0008, 0x1090)) or "").strip(),
        station_name=str(_get(first, (0x0008, 0x1010)) or "").strip(),
        protocol_name=str(_get(first, (0x0018, 0x1030)) or "").strip(),
        series_description=str(_get(first, (0x0008, 0x103E)) or "").strip(),
        study_instance_uid=str(_get(first, (0x0020, 0x000D)) or "").strip(),
        series_instance_uid=str(_get(first, (0x0020, 0x000E)) or "").strip(),
        source_root=Path(path),
        kvp=_as_float(_get(first, T_KVP)),
        tube_current_ma=np.asarray(currents, dtype=float),
        rotation_time_s=rot,
        pitch=pitch,
        collimation_mm=collim,
        table_feed_mm=feed,
        slice_thickness_mm=thickness,
        slice_locations_mm=np.asarray(locations, dtype=float),
        pixel_spacing_mm=pixel_spacing,
        rows=int(_get(first, T_ROWS) or 0) or None,
        columns=int(_get(first, T_COLUMNS) or 0) or None,
        data_collection_diameter_mm=_as_float(_get(first, (0x0018, 0x0090))),
        reconstruction_diameter_mm=_as_float(_get(first, (0x0018, 0x1100))),
        ctdivol_tag=ctdivol,
        dlp_tag=dlp,
        patient_sex=str(_get(first, (0x0010, 0x0040)) or "").strip(),
        patient_age_years=_parse_age(first),
        patient_weight_kg=_as_float(_get(first, (0x0010, 0x1030))),
        patient_size_m=_as_float(_get(first, (0x0010, 0x1020))),
        warnings=warnings,
    )
    if series.kvp is None:
        series.warnings.append(
            f"no KVP {_tag_str(T_KVP)}; the CTDI coefficient row cannot be selected"
        )
    return series
