"""Auditable alerts.

An alert here is never a bare verdict. Each one carries the metric, the reference it
was judged against, the size band it belongs to, the numeric excess, and the
provenance of the measured value -- so a reader can reconstruct the decision without
the software. That is the whole design constraint: no opaque classifier scores.

Optional statistical outlier flagging (:func:`outlier_alerts`) is deliberately a
separate, additive layer, and is reported alongside its own inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from .drl import DRLComparison
from .provenance import Quantity, Source


class Severity(str, Enum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"


#: Ratio thresholds for the DRL alert ladder.
NOTICE_RATIO = 1.0
WARNING_RATIO = 1.5


@dataclass(frozen=True)
class Alert:
    """One auditable finding."""

    severity: Severity
    code: str
    message: str
    metric: str = ""
    measured: float | None = None
    reference: float | None = None
    unit: str = ""
    ratio: float | None = None
    excess: float | None = None
    size_band: str = ""
    measured_source: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "metric": self.metric,
            "measured": self.measured,
            "reference": self.reference,
            "unit": self.unit,
            "ratio": self.ratio,
            "excess": self.excess,
            "size_band": self.size_band,
            "measured_source": self.measured_source,
            "evidence": self.evidence,
            "notes": list(self.notes),
        }


def size_band(diameter_cm: float | None) -> str:
    """Coarse water-equivalent-diameter band, used to qualify every DRL alert.

    A DRL comparison is only meaningful for a standard-sized patient; stating the band
    lets the reader discount an exceedance that is explained by patient size alone.
    """
    if diameter_cm is None:
        return "unknown"
    if diameter_cm < 20:
        return "small (D_w < 20 cm)"
    if diameter_cm < 26:
        return "below-average (20-26 cm)"
    if diameter_cm < 32:
        return "average (26-32 cm)"
    if diameter_cm < 38:
        return "above-average (32-38 cm)"
    return "large (D_w >= 38 cm)"


def _severity_for(ratio: float) -> Severity:
    if ratio >= WARNING_RATIO:
        return Severity.WARNING
    if ratio > NOTICE_RATIO:
        return Severity.NOTICE
    return Severity.INFO


def drl_alerts(
    comparisons: Iterable[DRLComparison],
    *,
    diameter_cm: float | None = None,
    include_within_drl: bool = False,
) -> list[Alert]:
    """Turn DRL comparisons into alerts, one per metric.

    Args:
        include_within_drl: also emit ``INFO`` alerts for metrics that are within the
            reference level. Useful for a full audit trail; off by default.
    """
    band = size_band(diameter_cm)
    out: list[Alert] = []
    for c in comparisons:
        sev = _severity_for(c.ratio)
        if sev is Severity.INFO and not include_within_drl:
            continue
        verb = "exceeds" if c.exceeds else "is within"
        out.append(
            Alert(
                severity=sev,
                code=f"drl.{c.metric.lower()}",
                message=(
                    f"{c.metric} {c.measured:.4g} {c.unit} {verb} the "
                    f"{c.entry.set_name} reference level for '{c.entry.examination}'"
                    + (f" ({c.entry.age_band})" if c.entry.age_band else "")
                    + f" of {c.reference:.4g} {c.unit} "
                    f"({c.percent_of_drl:.0f}% of DRL); patient size band: {band}"
                ),
                metric=c.metric,
                measured=c.measured,
                reference=c.reference,
                unit=c.unit,
                ratio=c.ratio,
                excess=c.excess,
                size_band=band,
                measured_source=c.measured_source,
                evidence={
                    "drl_set": c.entry.set_name,
                    "examination": c.entry.examination,
                    "age_band": c.entry.age_band,
                    "phantom_cm": c.entry.phantom_cm,
                    "diameter_cm": diameter_cm,
                },
                notes=tuple(c.notes) + tuple(c.entry.notes),
            )
        )
    return out


#: Quantities a reader would otherwise assume came from the scanner's own dose record.
#: SSDE, water-equivalent diameter and effective dose are computed by definition, so
#: flagging them says nothing; CTDIvol and DLP are the ones whose origin matters.
REPORTABLE_DOSE_INDICES = ("ctdivol", "dlp")


def _rests_on_a_fallback(q: Quantity) -> bool:
    if q.source is Source.PHYSICS_FALLBACK:
        return True
    return any(
        key.endswith("_source") and value == Source.PHYSICS_FALLBACK.value
        for key, value in q.inputs.items()
    )


def provenance_alerts(
    quantities: dict[str, Quantity],
    *,
    names: Sequence[str] = REPORTABLE_DOSE_INDICES,
) -> list[Alert]:
    """Flag reported dose indices that were reconstructed rather than recorded.

    In a survey this matters as much as the value itself: a cohort whose CTDIvol is
    half recorded and half reconstructed needs that split reported.

    Args:
        names: which quantities to consider. Defaults to CTDIvol and DLP; pass a wider
            set (or ``tuple(quantities)``) for a full audit trail.
    """
    out: list[Alert] = []
    for name in names:
        q = quantities.get(name)
        if not isinstance(q, Quantity) or not _rests_on_a_fallback(q):
            continue
        out.append(
            Alert(
                severity=Severity.INFO,
                code=f"provenance.{name}",
                message=(
                    f"{name} = {q.value:.4g} {q.unit} was reconstructed "
                    f"({q.source.value}), not read from a recorded dose value"
                ),
                metric=name,
                measured=q.value,
                unit=q.unit,
                measured_source=q.source.value,
                evidence={"formula": q.formula, "inputs": q.inputs, "reference": q.reference},
                notes=q.notes,
            )
        )
    return out


def outlier_alerts(
    value: float,
    cohort: Sequence[float],
    *,
    metric: str = "CTDIvol",
    unit: str = "mGy",
    z_threshold: float = 3.0,
) -> list[Alert]:
    """Flag a value as a cohort outlier using a robust (median/MAD) z-score.

    Reported as a separate, explainable statistic -- median, MAD and the resulting
    score are all in the evidence -- rather than as a black-box anomaly score.
    """
    arr = np.asarray([v for v in cohort if np.isfinite(v)], dtype=float)
    if arr.size < 5:
        return []
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    if mad == 0:
        return []
    score = 0.6745 * (value - median) / mad
    if abs(score) < z_threshold:
        return []
    direction = "above" if score > 0 else "below"
    return [
        Alert(
            severity=Severity.NOTICE,
            code=f"outlier.{metric.lower()}",
            message=(
                f"{metric} {value:.4g} {unit} is {abs(score):.1f} robust standard "
                f"deviations {direction} the cohort median of {median:.4g} {unit} "
                f"(n={arr.size})"
            ),
            metric=metric,
            measured=value,
            reference=median,
            unit=unit,
            ratio=value / median if median else None,
            excess=value - median,
            evidence={
                "method": "median/MAD robust z-score (0.6745*(x-median)/MAD)",
                "cohort_n": int(arr.size),
                "median": median,
                "mad": mad,
                "score": score,
                "z_threshold": z_threshold,
            },
        )
    ]


def summarise(alerts: Sequence[Alert]) -> dict[str, Any]:
    """Counts by severity plus the highest severity present."""
    counts = {s.value: sum(1 for a in alerts if a.severity is s) for s in Severity}
    highest = Severity.INFO
    for s in (Severity.WARNING, Severity.NOTICE):
        if counts[s.value]:
            highest = s
            break
    return {"counts": counts, "highest_severity": highest.value, "n": len(alerts)}
