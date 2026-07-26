"""Parse the CT Radiation Dose Structured Report (RDSR).

Many CT studies carry their as-delivered dose indices not in the image headers
but in a separate DICOM Structured Report object (Modality ``SR``), the *CT
Radiation Dose SR* (DICOM PS3.16 templates TID 10011/10013). This module reads
that object and returns the recorded CTDIvol, DLP, phantom size and acquisition
technique per irradiation event, each as a provenance-carrying
:class:`~ctdose_core.provenance.Quantity` with ``source = Source.RDSR``.

The parser is written against the DICOM standard's coded content items only
(``CodingSchemeDesignator == "DCM"`` plus the documented code values), so it is
vendor-neutral: GE, Siemens, Canon/Toshiba and Philips all populate the same
template. Nothing here is derived from any other implementation's source code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import guard only
    import pydicom
    from pydicom.dataset import Dataset
except ImportError as exc:  # pragma: no cover
    raise ImportError("ctdose-core requires pydicom") from exc

from .provenance import Quantity, Source

# --- DICOM (DCM) code values used in the CT Radiation Dose SR -------------------------
# Reference: DICOM PS3.16 CID 10013 / TID 10011-10013 (CT Radiation Dose).
DCM = "DCM"
C_XRAY_DOSE_REPORT = "113701"      # "X-Ray Radiation Dose Report" (SR title)
C_CT_ACCUMULATED = "113811"        # "CT Accumulated Dose Data"
C_DLP_TOTAL = "113813"             # "CT Dose Length Product Total"
C_TOTAL_EVENTS = "113812"          # "Total Number of Irradiation Events"
C_CT_ACQUISITION = "113819"        # "CT Acquisition"
C_ACQ_PROTOCOL = "125203"          # "Acquisition Protocol"
C_TARGET_REGION = "123014"         # "Target Region"
C_CT_ACQ_TYPE = "113820"           # "CT Acquisition Type"
C_CT_ACQ_PARAMS = "113822"         # "CT Acquisition Parameters"
C_EXPOSURE_TIME = "113824"         # "Exposure Time" (s)
C_SCANNING_LENGTH = "113825"       # "Scanning Length" (mm)
C_TOTAL_COLLIMATION = "113827"     # "Nominal Total Collimation Width" (mm)
C_PITCH = "113828"                 # "Pitch Factor"
C_CT_DOSE = "113829"               # "CT Dose"
C_MEAN_CTDIVOL = "113830"          # "Mean CTDIvol" (mGy)
C_CTDIW_PHANTOM = "113835"         # "CTDIw Phantom Type" (code: 16 cm / 32 cm)
C_DLP = "113838"                   # "DLP" (mGy.cm)
C_XRAY_SOURCE_PARAMS = "113831"    # "CT X-Ray Source Parameters"
C_KVP = "113733"                   # "KVP" (kV)
C_MAX_TUBE_CURRENT = "113833"      # "Maximum X-Ray Tube Current" (mA)
C_MEAN_TUBE_CURRENT = "113734"     # "Mean X-Ray Tube Current" (mA)  [see note]
C_EXPOSURE_TIME_PER_ROT = "113834"  # "Exposure Time per Rotation" (s)
C_MODULATION_TYPE = "113842"       # "X-Ray Modulation Type" (text)

# Some GE/older objects use 113505 "X-Ray Tube Current" for the mean; accept both.
C_XRAY_TUBE_CURRENT_ALT = "113505"


def _concept(item: Dataset) -> tuple[str, str, str]:
    """Return (scheme, code, meaning) of a content item's ConceptNameCodeSequence."""
    seq = getattr(item, "ConceptNameCodeSequence", None)
    if not seq:
        return ("", "", "")
    c = seq[0]
    return (
        str(getattr(c, "CodingSchemeDesignator", "")),
        str(getattr(c, "CodeValue", "")),
        str(getattr(c, "CodeMeaning", "")),
    )


def _num(item: Dataset) -> tuple[float | None, str]:
    """Return (numeric value, unit code) of a NUM content item."""
    mvs = getattr(item, "MeasuredValueSequence", None)
    if not mvs:
        return (None, "")
    mv = mvs[0]
    val = getattr(mv, "NumericValue", None)
    unit = ""
    ucs = getattr(mv, "MeasurementUnitsCodeSequence", None)
    if ucs:
        unit = str(getattr(ucs[0], "CodeValue", "")) or str(getattr(ucs[0], "CodeMeaning", ""))
    try:
        return (float(val), unit) if val is not None else (None, unit)
    except (TypeError, ValueError):
        return (None, unit)


def _code_meaning(item: Dataset) -> str:
    seq = getattr(item, "ConceptCodeSequence", None)
    if not seq:
        return ""
    return str(getattr(seq[0], "CodeMeaning", ""))


def _index(item: Dataset) -> dict[str, Dataset]:
    """Index an item's direct children by their DCM code value."""
    out: dict[str, Dataset] = {}
    for child in getattr(item, "ContentSequence", []) or []:
        scheme, code, _ = _concept(child)
        if scheme == DCM and code:
            out.setdefault(code, child)
    return out


@dataclass
class RdsrAcquisition:
    """One irradiation event's recorded dose and technique."""

    protocol: str = ""
    target_region: str = ""
    acquisition_type: str = ""
    ctdivol: Quantity | None = None
    dlp: Quantity | None = None
    phantom_type: str = ""          # e.g. "IEC Body Dosimetry Phantom" (32 cm)
    kvp: float | None = None
    mean_tube_current_ma: float | None = None
    max_tube_current_ma: float | None = None
    exposure_time_per_rotation_s: float | None = None
    modulation_type: str = ""
    pitch: float | None = None
    total_collimation_mm: float | None = None
    scanning_length_mm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if not isinstance(v, Quantity)}
        d["ctdivol"] = self.ctdivol.to_dict() if self.ctdivol else None
        d["dlp"] = self.dlp.to_dict() if self.dlp else None
        return d


@dataclass
class RdsrReport:
    """The parsed CT Radiation Dose SR."""

    manufacturer: str = ""
    model_name: str = ""
    station_name: str = ""
    study_date: str = ""
    total_dlp: Quantity | None = None
    acquisitions: list[RdsrAcquisition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manufacturer": self.manufacturer,
            "model_name": self.model_name,
            "station_name": self.station_name,
            "study_date": self.study_date,
            "total_dlp": self.total_dlp.to_dict() if self.total_dlp else None,
            "acquisitions": [a.to_dict() for a in self.acquisitions],
        }


def is_ct_rdsr(ds: Dataset) -> bool:
    """True if the dataset is a CT Radiation Dose Structured Report."""
    if str(getattr(ds, "Modality", "")).upper() != "SR":
        return False
    scheme, code, _ = _concept(ds)
    # Root concept is the X-Ray Radiation Dose Report; some objects only set the
    # SR title in ConceptNameCodeSequence, so accept either the title code or the
    # presence of a CT Acquisition / Accumulated-dose child.
    if scheme == DCM and code == C_XRAY_DOSE_REPORT:
        return True
    top = _index(ds)
    return C_CT_ACQUISITION in top or C_CT_ACCUMULATED in top


def _parse_acquisition(item: Dataset) -> RdsrAcquisition:
    acq = RdsrAcquisition()
    children = getattr(item, "ContentSequence", []) or []
    for child in children:
        scheme, code, _ = _concept(child)
        if scheme != DCM:
            continue
        vt = str(getattr(child, "ValueType", ""))
        if code == C_ACQ_PROTOCOL and vt == "TEXT":
            acq.protocol = str(getattr(child, "TextValue", "")).strip()
        elif code == C_TARGET_REGION and vt == "CODE":
            acq.target_region = _code_meaning(child)
        elif code == C_CT_ACQ_TYPE and vt == "CODE":
            acq.acquisition_type = _code_meaning(child)
        elif code == C_CT_ACQ_PARAMS and vt == "CONTAINER":
            _parse_acq_params(child, acq)
        elif code == C_CT_DOSE and vt == "CONTAINER":
            _parse_ct_dose(child, acq)
    return acq


def _parse_acq_params(item: Dataset, acq: RdsrAcquisition) -> None:
    for child in getattr(item, "ContentSequence", []) or []:
        scheme, code, _ = _concept(child)
        if scheme != DCM:
            continue
        if code == C_SCANNING_LENGTH:
            acq.scanning_length_mm, _ = _num(child)
        elif code == C_TOTAL_COLLIMATION:
            acq.total_collimation_mm, _ = _num(child)
        elif code == C_PITCH:
            acq.pitch, _ = _num(child)
        elif code == C_XRAY_SOURCE_PARAMS:
            _parse_source_params(child, acq)


def _parse_source_params(item: Dataset, acq: RdsrAcquisition) -> None:
    for child in getattr(item, "ContentSequence", []) or []:
        scheme, code, _ = _concept(child)
        if scheme != DCM:
            continue
        if code == C_KVP:
            acq.kvp, _ = _num(child)
        elif code == C_MAX_TUBE_CURRENT:
            acq.max_tube_current_ma, _ = _num(child)
        elif code in (C_MEAN_TUBE_CURRENT, C_XRAY_TUBE_CURRENT_ALT):
            acq.mean_tube_current_ma, _ = _num(child)
        elif code == C_EXPOSURE_TIME_PER_ROT:
            acq.exposure_time_per_rotation_s, _ = _num(child)
        elif code == C_MODULATION_TYPE:
            acq.modulation_type = str(getattr(child, "TextValue", "")).strip()


def _parse_ct_dose(item: Dataset, acq: RdsrAcquisition) -> None:
    for child in getattr(item, "ContentSequence", []) or []:
        scheme, code, _ = _concept(child)
        if scheme != DCM:
            continue
        if code == C_MEAN_CTDIVOL:
            v, unit = _num(child)
            if v is not None:
                acq.ctdivol = Quantity(
                    value=v, unit=unit or "mGy", source=Source.RDSR,
                    reference="DCM 113830 Mean CTDIvol",
                )
        elif code == C_DLP:
            v, unit = _num(child)
            if v is not None:
                acq.dlp = Quantity(
                    value=v, unit=unit or "mGycm", source=Source.RDSR,
                    reference="DCM 113838 DLP",
                )
        elif code == C_CTDIW_PHANTOM:
            acq.phantom_type = _code_meaning(child)


def parse_rdsr(ds: Dataset) -> RdsrReport:
    """Parse an already-loaded CT Radiation Dose SR dataset."""
    report = RdsrReport(
        manufacturer=str(getattr(ds, "Manufacturer", "")).strip(),
        model_name=str(getattr(ds, "ManufacturerModelName", "")).strip(),
        station_name=str(getattr(ds, "StationName", "")).strip(),
        study_date=str(getattr(ds, "StudyDate", "")).strip(),
    )
    for item in getattr(ds, "ContentSequence", []) or []:
        scheme, code, _ = _concept(item)
        if scheme != DCM:
            continue
        if code == C_CT_ACQUISITION and str(getattr(item, "ValueType", "")) == "CONTAINER":
            report.acquisitions.append(_parse_acquisition(item))
        elif code == C_CT_ACCUMULATED and str(getattr(item, "ValueType", "")) == "CONTAINER":
            acc = _index(item)
            dlp_item = acc.get(C_DLP_TOTAL)
            if dlp_item is not None:
                v, unit = _num(dlp_item)
                if v is not None:
                    report.total_dlp = Quantity(
                        value=v, unit=unit or "mGycm", source=Source.RDSR,
                        reference="DCM 113813 CT Dose Length Product Total",
                    )
    return report


def read_rdsr(path: str | Path) -> RdsrReport | None:
    """Read a CT Radiation Dose SR file. Returns None if it is not a CT RDSR."""
    ds = pydicom.dcmread(str(path), force=True)
    if not is_ct_rdsr(ds):
        return None
    return parse_rdsr(ds)
