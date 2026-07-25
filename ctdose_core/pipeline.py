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
from . import segmentation as seg
from . import ssde as ssde_mod
from .effdose import effective_dose
from .io import ScanSeries, load_series
from .provenance import Quantity, Source
from .scanners import resolve_model
from .tables import (
    HEAD_REGIONS,
    PEDIATRIC_KFACTOR_SIZES,
    SIZE_ADULT,
    SIZE_CHILD_7Y,
    SIZE_CHILD_8W,
    ConversionFactors,
    ScannerCoefficients,
    lookup_conversion,
    lookup_scanner,
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
    coefficients: ScannerCoefficients
    factors: ConversionFactors
    acquisition: metrics_mod.Acquisition
    quantities: dict[str, Quantity] = field(default_factory=dict)
    ssde_result: ssde_mod.SSDEResult | None = None
    drl_comparisons: list[drl_mod.DRLComparison] = field(default_factory=list)
    alerts: list[alert_mod.Alert] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

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
            "coefficients": {
                "nCTDIair_mGy_per_mAs": self.coefficients.nctdi_air,
                "nCTDIw_mGy_per_mAs": self.coefficients.nctdi_w,
                "k_CT": self.coefficients.ct_factor,
                "beam_width_ref_mm": self.coefficients.beam_width_ref_mm,
                "overbeam_dz_mm": self.coefficients.overbeam_dz_mm,
                "overrange_m": self.coefficients.overrange_m,
                "overrange_b": self.coefficients.overrange_b,
                "kV_reference": self.coefficients.kv_reference,
                "effective_dose_route": self.coefficients.effective_dose_route,
            },
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
            "warnings": list(self.warnings),
        }


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
    """
    series = path if isinstance(path, ScanSeries) else load_series(path, series_uid=series_uid)
    warnings = list(series.warnings)

    if scanner_model is None:
        scanner_model, model_warnings = resolve_model(
            series.model_name, series.manufacturer, bowtie=bowtie
        )
        warnings.extend(model_warnings)

    size_label = patient_size or patient_size_label(series.patient_age_years)
    coef = lookup_scanner(scanner_model, region, size_label)
    for flag in coef.quality_flags:
        warnings.append(f"coefficient table ({scanner_model}, {region}, {size_label}): {flag}")
    if coef.ct_factor_inherited_from:
        warnings.append(
            f"k_CT for this row is not set by the reference implementation; "
            f"inherited from the {coef.ct_factor_inherited_from}"
        )
    factors = lookup_conversion(region, size_label, series.patient_sex)
    acq = metrics_mod.acquisition_from_series(series)

    recorded_ctdivol = series.ctdivol_tag.value if series.ctdivol_tag else None
    recorded_dlp = series.dlp_tag.value if series.dlp_tag else None
    quantities = metrics_mod.dose_metrics(
        acq,
        coef,
        recorded_ctdivol_mgy=recorded_ctdivol,
        recorded_dlp_mgycm=recorded_dlp,
        prefer_recorded=prefer_recorded,
    )

    diameter: Quantity | None = None
    size_detail: dict[str, Any] = {}
    if measure_size:
        diameter, size_detail = measure_patient_size(series)
        if diameter is None:
            warnings.append(f"water-equivalent diameter unavailable: {size_detail.get('reason')}")

    ssde_result = None
    if diameter is not None:
        phantom = ssde_mod.phantom_for(region, float(diameter))
        ssde_result = ssde_mod.ssde(
            quantities["ctdivol"],
            diameter,
            phantom=phantom,
            region="head" if region in HEAD_REGIONS else "body",
        )
        quantities["water_equivalent_diameter"] = diameter
        quantities["ssde"] = ssde_result.ssde

    try:
        quantities["effective_dose"] = effective_dose(
            coef,
            factors,
            dlp_air=quantities.get("dlp_air"),
            dlp_mgycm=quantities["dlp"],
            kvp=series.kvp,
        )
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
        scanner_model=scanner_model,
        patient_size=size_label,
        coefficients=coef,
        factors=factors,
        acquisition=acq,
        quantities=quantities,
        ssde_result=ssde_result,
        drl_comparisons=comparisons,
        alerts=alerts,
        warnings=warnings,
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
