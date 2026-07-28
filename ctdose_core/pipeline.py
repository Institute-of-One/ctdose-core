"""End-to-end: a CT series directory in, an auditable dose report out.

This is the convenience layer. Everything it does is available piecewise from the
other modules; nothing is hidden in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import alert as alert_mod
from . import drl as drl_mod
from . import metrics as metrics_mod
from . import rdsr as rdsr_mod
from . import segmentation as seg
from . import ssde as ssde_mod
from .ctdi_table import CoefficientNotAvailable
from .ctdi_table import provenance as ctdi_provenance
from .ctdi_table import resolve_model as resolve_open_model
from .effdose import effective_dose
from .io import ScanSeries, load_series
from .provenance import Quantity, Source
from .tables import (
    HEAD_REGIONS,
    PEDIATRIC_KFACTOR_SIZES,
    SIZE_ADULT,
    SIZE_CHILD_7Y,
    SIZE_CHILD_8W,
    ConversionFactors,
    lookup_conversion,
)


def patient_size_label(age_years: float | None, *, prefer_kfactor: bool = False) -> str:
    """Map patient age to the reference implementation's patient-size label.

    Adults (>= 15 y) use ``"Adults"``. Children map to the nearest tabulated
    phantom -- the 8-week and 7-year phantoms on the DLP-in-air route, or the
    0/1/5/10-year ICRP-103 phantoms when ``prefer_kfactor`` is set.
    """
    if age_years is None or age_years >= 15:
        return SIZE_ADULT
    if prefer_kfactor:
        for label, upper in zip(
            PEDIATRIC_KFACTOR_SIZES, (0.5, 3.0, 7.5, float("inf")), strict=True
        ):
            if age_years < upper:
                return label
    return SIZE_CHILD_8W if age_years < 1.0 else SIZE_CHILD_7Y


@dataclass
class DoseReport:
    """Every number the pipeline produced, plus how it was produced."""

    series: ScanSeries
    region: str
    scanner_model: str
    patient_size: str
    coefficients: dict[str, Any]
    factors: ConversionFactors
    acquisition: metrics_mod.Acquisition
    quantities: dict[str, Quantity] = field(default_factory=dict)
    ssde_result: ssde_mod.SSDEResult | None = None
    drl_comparisons: list[drl_mod.DRLComparison] = field(default_factory=list)
    alerts: list[alert_mod.Alert] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: The dose SR used, the event it was matched to, and how confident that match is.
    rdsr: rdsr_mod.RdsrReport | None = None
    rdsr_match: rdsr_mod.AcquisitionMatch | None = None
    #: Every recorded value found, whichever source won. Keyed by quantity name.
    recorded_candidates: dict[str, list[Quantity]] = field(default_factory=dict)
    #: RDSR-vs-image-header comparisons for quantities recorded in both places.
    source_agreement: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def ctdivol(self) -> Quantity:
        return self.quantities["ctdivol"]

    @property
    def dlp(self) -> Quantity:
        return self.quantities["dlp"]

    @property
    def ssde(self) -> Quantity | None:
        return self.ssde_result.ssde if self.ssde_result else None

    @property
    def effective_dose(self) -> Quantity | None:
        return self.quantities.get("effective_dose")

    def to_dict(self) -> dict[str, Any]:
        return {
            "series": self.series.summary(),
            "region": self.region,
            "scanner_model": self.scanner_model,
            "patient_size": self.patient_size,
            "coefficients": self.coefficients,
            "conversion_factors": {
                "region": self.factors.region,
                "size": self.factors.size,
                "sex": self.factors.sex,
                "f_mean": self.factors.fmean,
                "k_factor": self.factors.kfactor,
            },
            "quantities": {k: v.to_dict() for k, v in self.quantities.items()},
            "ssde": self.ssde_result.to_dict() if self.ssde_result else None,
            "drl": [c.to_dict() for c in self.drl_comparisons],
            "alerts": [a.to_dict() for a in self.alerts],
            "alert_summary": alert_mod.summarise(self.alerts),
            "rdsr": self.rdsr.to_dict() if self.rdsr else None,
            "rdsr_match": self.rdsr_match.to_dict() if self.rdsr_match else None,
            "recorded_candidates": {
                name: [q.to_dict() for q in qs] for name, qs in self.recorded_candidates.items()
            },
            "source_agreement": self.source_agreement,
            "warnings": list(self.warnings),
        }


@dataclass
class RecordedDose:
    """Every recorded CTDIvol/DLP found for a series, and which one wins."""

    ctdivol_candidates: list[Quantity] = field(default_factory=list)
    dlp_candidates: list[Quantity] = field(default_factory=list)
    report: rdsr_mod.RdsrReport | None = None
    match: rdsr_mod.AcquisitionMatch | None = None
    phantom_cm: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ctdivol(self) -> Quantity | None:
        return metrics_mod.most_authoritative(self.ctdivol_candidates)

    @property
    def dlp(self) -> Quantity | None:
        return metrics_mod.most_authoritative(self.dlp_candidates)

    def agreement(self) -> dict[str, dict[str, Any]]:
        """Pairwise comparison whenever a quantity was recorded in more than one place."""
        out: dict[str, dict[str, Any]] = {}
        for name, candidates in (("ctdivol", self.ctdivol_candidates), ("dlp", self.dlp_candidates)):
            if len(candidates) < 2:
                continue
            winner = metrics_mod.most_authoritative(candidates)
            others = [q for q in candidates if q is not winner]
            out[name] = metrics_mod.agreement(winner, others[0])
            if len(others) > 1:
                out[name]["additional"] = [
                    metrics_mod.agreement(winner, q) for q in others[1:]
                ]
        return out


#: Relative disagreement between an RDSR and an image-header dose value above which the
#: event match is treated as suspect rather than as a rounding difference.
SOURCE_DISAGREEMENT_TOLERANCE = 0.02


def collect_recorded_dose(
    series: ScanSeries,
    *,
    use_rdsr: bool = True,
    rdsr_path: Path | str | None = None,
) -> RecordedDose:
    """Gather every recorded CTDIvol/DLP for a series, from the SR and the image header.

    Both origins are kept. The RDSR outranks the image header (it is the scanner's own
    per-event dose record, written for exactly this purpose), but the header value is
    not discarded: the two are compared, and a disagreement is reported because it
    usually means the SR event was matched to the wrong series.

    Args:
        use_rdsr: search for a CT Radiation Dose SR. Set False to force the image-header
            and physics routes only.
        rdsr_path: an explicit SR file or directory, bypassing the search.
    """
    out = RecordedDose()

    if series.ctdivol_tag and series.ctdivol_tag.value:
        out.ctdivol_candidates.append(
            Quantity(
                value=float(series.ctdivol_tag.value),
                unit="mGy",
                source=Source.DICOM_TAG,
                formula="CTDI Volume (0018,9345)",
                inputs={"tag": series.ctdivol_tag.tag},
            )
        )
    if series.dlp_tag and series.dlp_tag.value:
        out.dlp_candidates.append(
            Quantity(
                value=float(series.dlp_tag.value),
                unit="mGy*cm",
                source=Source.DICOM_TEXT,
                formula="recorded dose-length product",
                inputs={"tag": series.dlp_tag.tag, "text": series.dlp_tag.note},
            )
        )

    if not use_rdsr:
        return out

    reports: list[rdsr_mod.RdsrReport] = []
    if rdsr_path is not None:
        p = Path(rdsr_path)
        if p.is_file():
            report = rdsr_mod.read_rdsr(p)
            if report is None:
                out.warnings.append(f"{p} is not a CT Radiation Dose SR")
            else:
                reports = [report]
        else:
            reports = rdsr_mod.find_rdsr(p)
    elif series.source_root is not None:
        reports = rdsr_mod.find_rdsr(
            series.source_root, study_instance_uid=series.study_instance_uid or None
        )
    if not reports:
        return out

    if len(reports) > 1:
        out.warnings.append(
            f"{len(reports)} CT Radiation Dose SR objects found for this study; "
            f"using {reports[0].path.name if reports[0].path else 'the first'}"
        )
    out.report = reports[0]
    out.match = rdsr_mod.match_acquisition(
        out.report,
        protocol_name=series.protocol_name,
        series_description=series.series_description,
        scanning_length_mm=series.scan_range_mm or None,
    )
    out.warnings.extend(out.match.notes)

    acq = out.match.acquisition
    if acq is None:
        out.warnings.append(
            "a dose SR was found but no irradiation event could be attributed to this "
            "series; recorded values from it are not used"
        )
        return out

    out.phantom_cm = rdsr_mod.phantom_cm(acq.phantom_type)
    if acq.ctdivol:
        out.ctdivol_candidates.append(
            Quantity(
                value=float(acq.ctdivol),
                unit="mGy",
                source=Source.RDSR,
                formula="CT Radiation Dose SR, Mean CTDIvol",
                inputs={
                    "code": "DCM 113830",
                    "protocol": acq.protocol,
                    "phantom": acq.phantom_type,
                    "phantom_cm": out.phantom_cm,
                    "match_method": out.match.method,
                    "match_confidence": out.match.confidence,
                },
                reference="DICOM PS3.16 TID 10013",
            )
        )
    if acq.dlp:
        out.dlp_candidates.append(
            Quantity(
                value=float(acq.dlp),
                unit="mGy*cm",
                source=Source.RDSR,
                formula="CT Radiation Dose SR, DLP",
                inputs={
                    "code": "DCM 113838",
                    "protocol": acq.protocol,
                    "match_method": out.match.method,
                    "match_confidence": out.match.confidence,
                },
                reference="DICOM PS3.16 TID 10013",
            )
        )
    return out


def measure_patient_size(
    series: ScanSeries,
    *,
    slice_index: int | None = None,
    n_slices: int = 5,
) -> tuple[Quantity, dict[str, Any]] | tuple[None, dict[str, Any]]:
    """Water-equivalent diameter from the image data.

    By default the central slice and its neighbours are contoured and the median
    water-equivalent diameter is taken, which is more stable than the single central
    slice the reference implementation used while remaining cheap.

    Returns:
        ``(diameter, detail)``; ``diameter`` is ``None`` when the pixel data or pixel
        spacing is unavailable.
    """
    if series.pixel_spacing_mm is None:
        return None, {"reason": "no Pixel Spacing (0028,0030)"}
    try:
        volume = series.load_volume()
    except Exception as exc:  # pragma: no cover - depends on external data
        return None, {"reason": f"pixel data unreadable: {exc}"}

    centre = seg.central_slice_index(volume) if slice_index is None else slice_index
    half = max(0, n_slices // 2)
    indices = [i for i in range(centre - half, centre + half + 1) if 0 <= i < volume.shape[0]]

    diameters: list[float] = []
    contours = []
    for i in indices:
        c = seg.contour_metrics(volume[i], series.pixel_spacing_mm)
        if c.is_empty:
            continue
        contours.append(c)
        diameters.append(float(ssde_mod.water_equivalent_diameter(c.mean_hu, c.area_cm2)))
    if not diameters:
        return None, {"reason": "body contour could not be extracted"}

    value = float(np.median(diameters))
    detail = {
        "slice_indices": indices,
        "per_slice_dw_cm": diameters,
        "central_slice_index": centre,
        "mean_hu": [c.mean_hu for c in contours],
        "area_cm2": [c.area_cm2 for c in contours],
        "ap_cm": [c.ap_cm for c in contours],
        "lateral_cm": [c.lateral_cm for c in contours],
    }
    quantity = Quantity(
        value=value,
        unit="cm",
        source=Source.DERIVED,
        formula="median over slices of D_w = 2*sqrt((HU_mean/1000+1)*A_ROI/pi)",
        inputs={"n_slices": len(diameters), "central_slice_index": centre},
        reference="AAPM TG-220 (2014)",
    )
    return quantity, detail


def analyze_series(
    path: Path | str | ScanSeries,
    *,
    region: str,
    scanner_model: str | None = None,
    bowtie: str | None = None,
    patient_size: str | None = None,
    drl_key: str | None = None,
    measure_size: bool = True,
    prefer_recorded: bool = True,
    series_uid: str | None = None,
    use_rdsr: bool = True,
    rdsr_path: Path | str | None = None,
) -> DoseReport:
    """Run the full dose analysis on a CT series.

    Args:
        path: series directory, single file, or an already-loaded :class:`ScanSeries`.
        region: body region label, e.g. ``"Chest"``, ``"Whole abdomen"``, ``"Head"``.
            Must be one of the conversion table's regions.
        scanner_model: coefficient-table model name. Inferred from the DICOM header
            when omitted.
        bowtie: ``"small"``/``"medium"``/``"large"`` for scanners whose coefficients
            depend on the scan field of view.
        patient_size: override the age-derived patient-size label.
        drl_key: DRL examination key to compare against, e.g. ``"routine_chest"``.
            No DRL comparison is made when omitted.
        measure_size: contour the images to obtain a water-equivalent diameter.
            Set False to skip pixel-data loading.
        prefer_recorded: use recorded CTDIvol/DLP when present. Set False to force the
            physics fallback -- which is how the fallback is validated against series
            that do carry the tags.
        use_rdsr: look for a CT Radiation Dose SR beside the series and prefer its
            recorded dose over the image header.
        rdsr_path: an explicit SR file or directory, bypassing the search.
    """
    series = path if isinstance(path, ScanSeries) else load_series(path, series_uid=series_uid)
    warnings = list(series.warnings)

    # The open coefficient database names 17 scanners; anything else is unrecoverable by
    # reconstruction, and saying so is the honest outcome. Bowtie is no longer inferred
    # from the scan field of view -- it is a lookup key, so an unrecorded bowtie is
    # resolved against what the table measured and declared on the value.
    open_model: tuple[str, str] | None = None
    try:
        open_model = resolve_open_model(series.manufacturer, series.model_name)
    except CoefficientNotAvailable as exc:
        warnings.append(str(exc))

    size_label = patient_size or patient_size_label(series.patient_age_years)
    factors = lookup_conversion(region, size_label, series.patient_sex)
    acq = metrics_mod.acquisition_from_series(series)

    recorded = collect_recorded_dose(series, use_rdsr=use_rdsr, rdsr_path=rdsr_path)
    warnings.extend(recorded.warnings)
    # CTDIvol: a recorded value when one exists, otherwise the open-table reconstruction.
    quantities: dict[str, Quantity] = {}
    coefficients: dict[str, Any] = {"source": "none"}
    reconstructed: Quantity | None = None
    if open_model is not None:
        phantom_guess = ssde_mod.phantom_for(region, None)
        try:
            reconstructed = metrics_mod.estimate_ctdivol_open(
                acq,
                vendor=open_model[0],
                model=open_model[1],
                phantom_cm=int(phantom_guess),
            )
            coefficients = {
                "source": ctdi_provenance()["name"],
                "citation": ctdi_provenance()["citation"],
                "license": ctdi_provenance()["license"],
                "table_model": f"{open_model[0]} {open_model[1]}",
            }
        except CoefficientNotAvailable as exc:
            warnings.append(f"CTDIvol not reconstructable: {exc}")

    # prefer_recorded=False deliberately forces the reconstruction, which is how the
    # fallback is exercised on series that do carry a recorded value -- the comparison the
    # accuracy arm rests on. Taking the first candidate would silently return the recorded
    # value instead and make that comparison vacuous.
    candidates = [q for q in (recorded.ctdivol, reconstructed) if q is not None]
    if not prefer_recorded and reconstructed is not None:
        quantities["ctdivol"] = reconstructed
    elif candidates:
        quantities["ctdivol"] = metrics_mod.most_authoritative(candidates) or candidates[0]

    # DLP: recorded when present, otherwise CTDIvol times the imaged length.
    if recorded.dlp is not None:
        quantities["dlp"] = recorded.dlp
    elif "ctdivol" in quantities and series.scan_range_mm:
        quantities["dlp"] = metrics_mod.estimate_dlp_open(
            quantities["ctdivol"], series.scan_range_mm
        )

    source_agreement = recorded.agreement()
    for name, cmp in source_agreement.items():
        pct = cmp.get("percent_difference")
        if pct is not None and abs(pct) > 100 * SOURCE_DISAGREEMENT_TOLERANCE:
            warnings.append(
                f"{name}: the {cmp['a']['source']} value {cmp['a']['value']:.4g} and the "
                f"{cmp['b']['source']} value {cmp['b']['value']:.4g} differ by "
                f"{pct:+.1f}%; the dose SR event may not correspond to this series"
            )

    diameter: Quantity | None = None
    size_detail: dict[str, Any] = {}
    if measure_size:
        diameter, size_detail = measure_patient_size(series)
        if diameter is None:
            warnings.append(f"water-equivalent diameter unavailable: {size_detail.get('reason')}")

    ssde_result = None
    if diameter is not None:
        # A CTDIvol from an RDSR states its own reference phantom; trust that over the
        # region-based guess, because using the wrong phantom mis-scales the SSDE.
        if recorded.phantom_cm is not None and quantities["ctdivol"].source is Source.RDSR:
            phantom = recorded.phantom_cm
        else:
            phantom = ssde_mod.phantom_for(region, float(diameter))
        ssde_result = ssde_mod.ssde(
            quantities["ctdivol"],
            diameter,
            phantom=phantom,
            region="head" if region in HEAD_REGIONS else "body",
        )
        quantities["water_equivalent_diameter"] = diameter
        quantities["ssde"] = ssde_result.ssde

    if "dlp" in quantities:
        try:
            quantities["effective_dose"] = effective_dose(quantities["dlp"], factors)
        except ValueError as exc:
            warnings.append(f"effective dose not computed: {exc}")

    comparisons: list[drl_mod.DRLComparison] = []
    if drl_key:
        entry = drl_mod.lookup(drl_key, age_years=series.patient_age_years)
        comparisons = drl_mod.compare(
            entry,
            ctdivol_mgy=float(quantities["ctdivol"]),
            dlp_mgycm=float(quantities["dlp"]),
            ctdivol_source=quantities["ctdivol"].source.value,
            dlp_source=quantities["dlp"].source.value,
        )

    alerts = alert_mod.drl_alerts(
        comparisons, diameter_cm=float(diameter) if diameter is not None else None
    )
    alerts += alert_mod.provenance_alerts(quantities)

    report = DoseReport(
        series=series,
        region=region,
        scanner_model=(f"{open_model[0]} {open_model[1]}" if open_model else ""),
        patient_size=size_label,
        coefficients=coefficients,
        factors=factors,
        acquisition=acq,
        quantities=quantities,
        ssde_result=ssde_result,
        drl_comparisons=comparisons,
        alerts=alerts,
        warnings=warnings,
        rdsr=recorded.report,
        rdsr_match=recorded.match,
        recorded_candidates={
            "ctdivol": recorded.ctdivol_candidates,
            "dlp": recorded.dlp_candidates,
        },
        source_agreement=source_agreement,
    )
    if size_detail:
        report.quantities.setdefault(
            "size_measurement_detail",
            Quantity(
                value=float(diameter) if diameter is not None else float("nan"),
                unit="cm",
                source=Source.DERIVED,
                formula="per-slice water-equivalent diameter measurements",
                inputs=size_detail,
            ),
        )
    return report
