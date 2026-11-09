"""ctdose-core: open, auditable CT dose surveillance from DICOM.

Reads a CT series, produces CTDIvol, DLP, SSDE and effective dose -- reconstructing
the dose indices from acquisition physics when the DICOM dose attributes are absent,
which is the common case in public archives -- and compares the result against
diagnostic reference levels with a fully traceable audit trail.

Quick start::

    from ctdose_core import analyze_series

    report = analyze_series("path/to/series", region="Chest", drl_key="routine_chest")
    print(report.ctdivol, report.ctdivol.source)
    for a in report.alerts:
        print(a.severity.value, a.message)

Every returned number is a :class:`~ctdose_core.provenance.Quantity` that knows its
formula, inputs and whether it was recorded or reconstructed.
"""

from __future__ import annotations

__version__ = "0.2.0"

from .alert import Alert, Severity, drl_alerts, outlier_alerts, provenance_alerts, size_band
from .drl import DRLComparison, DRLEntry
from .drl import compare as compare_drl
from .drl import lookup as lookup_drl
from .effdose import effective_dose
from .io import ScanSeries, load_series
from .metrics import (
    Acquisition,
    acquisition_from_series,
    effective_dose_open,
    estimate_ctdivol_open,
    estimate_dlp_open,
)
from .pipeline import DoseReport, analyze_series, measure_patient_size, patient_size_label
from .provenance import Quantity, Source
from .scanners import match_models, resolve_model
from .segmentation import BodyContour, body_mask, contour_metrics
from .ssde import (
    SSDEResult,
    conversion_factor,
    effective_diameter,
    effective_diameter_from_measurement,
    head_conversion_factor,
    ssde,
    water_equivalent_diameter,
)
from .tables import (
    ConversionFactors,
    UnknownScannerError,
    lookup_conversion,
)

__all__ = [
    "__version__",
    # provenance
    "Quantity",
    "Source",
    # ingest
    "ScanSeries",
    "load_series",
    # metrics
    "Acquisition",
    "acquisition_from_series",
    "effective_dose_open",
    "estimate_ctdivol_open",
    "estimate_dlp_open",
    # size / SSDE
    "BodyContour",
    "body_mask",
    "contour_metrics",
    "water_equivalent_diameter",
    "effective_diameter",
    "effective_diameter_from_measurement",
    "conversion_factor",
    "head_conversion_factor",
    "ssde",
    "SSDEResult",
    # effective dose
    "effective_dose",
    # DRL and alerts
    "DRLEntry",
    "DRLComparison",
    "lookup_drl",
    "compare_drl",
    "Alert",
    "Severity",
    "drl_alerts",
    "provenance_alerts",
    "outlier_alerts",
    "size_band",
    # tables
    "ConversionFactors",
    "UnknownScannerError",
    "lookup_conversion",
    "match_models",
    "resolve_model",
    # pipeline
    "DoseReport",
    "analyze_series",
    "measure_patient_size",
    "patient_size_label",
]
