"""Size-specific dose estimates (AAPM TG-204) and patient size metrics.

Two size metrics are supported:

* **effective diameter** ``D_eff = sqrt(AP * LAT)`` -- purely geometric;
* **water-equivalent diameter** ``D_w = 2*sqrt((HU_mean/1000 + 1) * A_ROI / pi)``
  -- attenuation-based, and the one TG-204/TG-220 prefers because it accounts for
  tissue composition rather than outline alone.

Conversion factors come from the shipped TG-204 tables; the head conversion is the
published closed form, which gives the test suite an exact reference to check
against rather than a self-consistency check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .provenance import Quantity, Source
from .tables import ssde_tables

Phantom = Literal[16, 32]

#: AAPM TG-204 closed form for the 16 cm head phantom.
HEAD_A, HEAD_B = 1.9852, -0.0486


def head_conversion_factor(diameter_cm: float) -> Quantity:
    """Closed-form head SSDE conversion factor, ``f = 1.9852 * exp(-0.0486 * D)``.

    Args:
        diameter_cm: water-equivalent (or effective) diameter, cm.
    """
    value = HEAD_A * math.exp(HEAD_B * diameter_cm)
    return Quantity(
        value=value,
        unit="dimensionless",
        source=Source.DERIVED,
        formula="f_head = 1.9852 * exp(-0.0486 * D_water)",
        inputs={"D_cm": diameter_cm},
        reference="AAPM TG-204 / AAPM Report 293 (head SSDE)",
    )


def _table(phantom: Phantom) -> np.ndarray:
    key = "etab16" if phantom == 16 else "etab32"
    return np.asarray(ssde_tables()[key], dtype=float)


def conversion_factor(
    diameter_cm: float,
    phantom: Phantom = 32,
    *,
    region: str = "body",
    interpolate: bool = True,
) -> Quantity:
    """SSDE conversion factor for a patient size and reference phantom.

    Args:
        diameter_cm: water-equivalent or effective diameter, cm.
        phantom: 16 (head) or 32 (body) cm CTDI reference phantom.
        region: ``"head"`` selects the closed-form head conversion; anything else
            uses the tabulated TG-204 factors.
        interpolate: linearly interpolate between tabulated diameters. The reference
            implementation required an exact integer match and errored otherwise;
            interpolating (and clamping at the table ends) is a deliberate
            improvement, flagged in the returned notes when it changes the result.

    Returns:
        The conversion factor, with the lookup recorded in ``inputs``.
    """
    if region.lower() == "head":
        return head_conversion_factor(diameter_cm)

    tab = _table(phantom)
    d, f = tab[:, 0], tab[:, 1]
    notes: list[str] = []
    if diameter_cm < d[0]:
        value = float(f[0])
        notes.append(f"diameter {diameter_cm:.1f} cm below table range; clamped to {d[0]:.0f} cm")
    elif diameter_cm > d[-1]:
        value = float(f[-1])
        notes.append(f"diameter {diameter_cm:.1f} cm above table range; clamped to {d[-1]:.0f} cm")
    elif interpolate:
        value = float(np.interp(diameter_cm, d, f))
    else:
        idx = int(np.argmin(np.abs(d - diameter_cm)))
        value = float(f[idx])
        if d[idx] != diameter_cm:
            notes.append(f"nearest tabulated diameter {d[idx]:.0f} cm used")
    return Quantity(
        value=value,
        unit="dimensionless",
        source=Source.DERIVED,
        formula=f"TG-204 lookup, {phantom} cm phantom",
        inputs={"D_cm": diameter_cm, "phantom_cm": phantom, "interpolated": interpolate},
        reference="AAPM TG-204 (2011), Table 1 (effective diameter)",
        notes=tuple(notes),
    )


def effective_diameter(ap_cm: float, lateral_cm: float) -> Quantity:
    """Geometric effective diameter, ``sqrt(AP * LAT)``."""
    if ap_cm <= 0 or lateral_cm <= 0:
        raise ValueError("AP and lateral dimensions must be positive")
    return Quantity(
        value=math.sqrt(ap_cm * lateral_cm),
        unit="cm",
        source=Source.DERIVED,
        formula="D_eff = sqrt(AP * LAT)",
        inputs={"AP_cm": ap_cm, "LAT_cm": lateral_cm},
        reference="AAPM TG-204 (2011)",
    )


#: Which lookup table serves each single-dimension measurement.
_DIMENSION_TABLES = {
    "ap": "aptab",
    "lateral": "lattab",
    "ap+lateral": "aplattab",
}


def effective_diameter_from_measurement(
    value_cm: float, dimension: str = "ap", phantom: Phantom = 32
) -> Quantity:
    """Population-mean effective diameter from a single measured dimension.

    Useful when only a localiser measurement is available, e.g. from a scanogram.

    Args:
        value_cm: the measured dimension in cm.
        dimension: ``"ap"``, ``"lateral"`` or ``"ap+lateral"`` (the sum of the two).
        phantom: 16 or 32 cm reference phantom.

    Returns:
        The effective diameter in cm. Accuracy is that of a population regression, not
        of a per-patient measurement; prefer :func:`water_equivalent_diameter` when the
        images are available.
    """
    key = dimension.lower().replace(" ", "")
    if key not in _DIMENSION_TABLES:
        raise ValueError(f"dimension must be one of {sorted(_DIMENSION_TABLES)}, got {dimension!r}")
    tab = np.asarray(ssde_tables()[f"{_DIMENSION_TABLES[key]}{phantom}"], dtype=float)
    measured, d_eff = tab[:, 0], tab[:, 1]
    notes: list[str] = []
    if value_cm < measured[0] or value_cm > measured[-1]:
        notes.append(
            f"{dimension} measurement {value_cm:.1f} cm is outside the tabulated range "
            f"{measured[0]:.1f}-{measured[-1]:.1f} cm; clamped"
        )
    value = float(np.interp(value_cm, measured, d_eff))
    return Quantity(
        value=value,
        unit="cm",
        source=Source.DERIVED,
        formula=f"AAPM TG-204 {dimension} -> effective diameter, {phantom} cm phantom",
        inputs={f"{key}_cm": value_cm, "phantom_cm": phantom},
        reference="AAPM TG-204 (2011)",
        notes=tuple(notes),
    )


def water_equivalent_diameter(mean_hu: float, area_cm2: float) -> Quantity:
    """Water-equivalent diameter from a body ROI.

    ``D_w = 2 * sqrt((HU_mean/1000 + 1) * A_ROI / pi)``

    Args:
        mean_hu: mean Hounsfield value inside the body contour.
        area_cm2: contour area in cm^2.
    """
    if area_cm2 <= 0:
        raise ValueError("area_cm2 must be positive")
    scale = mean_hu / 1000.0 + 1.0
    if scale <= 0:
        raise ValueError(f"mean HU {mean_hu} implies non-physical attenuation")
    value = 2.0 * math.sqrt(scale * area_cm2 / math.pi)
    return Quantity(
        value=value,
        unit="cm",
        source=Source.DERIVED,
        formula="D_w = 2*sqrt((HU_mean/1000 + 1) * A_ROI / pi)",
        inputs={"HU_mean": mean_hu, "A_ROI_cm2": area_cm2},
        reference="AAPM TG-220 (2014); as implemented in the reference implementation",
    )


@dataclass(frozen=True)
class SSDEResult:
    """SSDE together with the size metric and conversion factor that produced it."""

    ssde: Quantity
    diameter: Quantity
    factor: Quantity
    phantom_cm: int
    region: str

    def to_dict(self) -> dict:
        return {
            "ssde": self.ssde.to_dict(),
            "diameter": self.diameter.to_dict(),
            "conversion_factor": self.factor.to_dict(),
            "phantom_cm": self.phantom_cm,
            "region": self.region,
        }


def ssde(
    ctdivol_mgy: Quantity | float,
    diameter: Quantity | float,
    *,
    phantom: Phantom = 32,
    region: str = "body",
    interpolate: bool = True,
) -> SSDEResult:
    """Size-specific dose estimate, ``SSDE = CTDIvol * f(D)``.

    Args:
        ctdivol_mgy: CTDIvol in the matching reference phantom.
        diameter: water-equivalent or effective diameter in cm.
        phantom: reference phantom the CTDIvol refers to (16 or 32 cm).
        region: ``"head"`` uses the closed-form conversion.
    """
    d = float(diameter)
    f = conversion_factor(d, phantom, region=region, interpolate=interpolate)
    c = float(ctdivol_mgy)
    ctdi_source = ctdivol_mgy.source if isinstance(ctdivol_mgy, Quantity) else Source.USER
    d_quantity = (
        diameter
        if isinstance(diameter, Quantity)
        else Quantity(d, "cm", Source.USER, formula="supplied by caller")
    )
    value = Quantity(
        value=c * float(f),
        unit="mGy",
        source=Source.DERIVED,
        formula="SSDE = CTDIvol * f(D)",
        inputs={
            "CTDIvol_mGy": c,
            "CTDIvol_source": ctdi_source.value,
            "f": float(f),
            "D_cm": d,
            "phantom_cm": phantom,
        },
        reference="AAPM TG-204 (2011)",
        notes=f.notes,
    )
    return SSDEResult(ssde=value, diameter=d_quantity, factor=f, phantom_cm=int(phantom), region=region)


def phantom_for(region: str, diameter_cm: float | None = None) -> Phantom:
    """Pick the reference phantom the way the reference implementation does.

    Head/neck examinations use the 16 cm phantom; body examinations use 32 cm, except
    that small patients (effective diameter <= 16 cm, i.e. infants) are also scanned
    and reported against the 16 cm phantom.
    """
    if region in ("Head", "Neck", "Head and Neck"):
        return 16
    if diameter_cm is not None and diameter_cm <= 16:
        return 16
    return 32
