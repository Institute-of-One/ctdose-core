"""Effective dose, by the two routes the reference implementation supports.

**DLP-in-air route** (adults and the two tabulated paediatric phantoms)::

    E = DLP_air * f_mean * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5

**ICRP-103 k-factor route** (the 0/1/5/10-year-old paediatric phantoms)::

    E = DLP * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5 * k

Both are region- and sex-dependent through the shipped conversion-factor table.
"""

from __future__ import annotations

from .geometry import kv_corrections
from .provenance import Quantity, Source
from .tables import ConversionFactors, ScannerCoefficients


def _kv_terms(kvp: float | None, coef: ScannerCoefficients) -> tuple[float, float, dict]:
    if kvp is None:
        return 1.0, 1.0, {"kVp": None, "note": "no KVP available; tube-voltage correction skipped"}
    k_ctdi, k_organ = kv_corrections(kvp, coef.kv_reference)
    return float(k_ctdi), float(k_organ), {"kVp": kvp, "kV_ref": coef.kv_reference}


def effective_dose_from_dlp_air(
    dlp_air: Quantity | float,
    coef: ScannerCoefficients,
    factors: ConversionFactors,
    *,
    kvp: float | None = None,
) -> Quantity:
    """``E = DLP_air * f_mean * k_CT * k_kV(CTDI) * k_kV(organ)``."""
    if factors.fmean is None:
        raise ValueError(
            f"no f_mean conversion factor for region={factors.region!r} size={factors.size!r}; "
            "use the k-factor route instead"
        )
    k_ctdi, k_organ, kv_inputs = _kv_terms(kvp, coef)
    d = float(dlp_air)
    value = d * factors.fmean * coef.ct_factor * k_ctdi * k_organ
    return Quantity(
        value=value,
        unit="mSv",
        source=Source.DERIVED,
        formula="E = DLP_air * f_mean * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5",
        inputs={
            "DLP_air_mGycm": d,
            "f_mean": factors.fmean,
            "k_CT": coef.ct_factor,
            "k_kV_CTDI": k_ctdi,
            "k_kV_organ": k_organ,
            "region": factors.region,
            "size": factors.size,
            "sex": factors.sex,
            **kv_inputs,
        },
        reference="CT-Expo organ-dose conversion (ICRP-103 tissue weighting)",
    )


def effective_dose_from_dlp(
    dlp_mgycm: Quantity | float,
    coef: ScannerCoefficients,
    factors: ConversionFactors,
    *,
    kvp: float | None = None,
) -> Quantity:
    """``E = DLP * k_CT * k_kV(CTDI) * k_kV(organ) * k`` (ICRP-103 k-factor route)."""
    if factors.kfactor is None:
        raise ValueError(
            f"no k-factor for region={factors.region!r} size={factors.size!r}"
        )
    k_ctdi, k_organ, kv_inputs = _kv_terms(kvp, coef)
    d = float(dlp_mgycm)
    value = d * coef.ct_factor * k_ctdi * k_organ * factors.kfactor
    dlp_source = dlp_mgycm.source if isinstance(dlp_mgycm, Quantity) else Source.USER
    return Quantity(
        value=value,
        unit="mSv",
        source=Source.DERIVED,
        formula="E = DLP * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5 * k",
        inputs={
            "DLP_mGycm": d,
            "DLP_source": dlp_source.value,
            "k": factors.kfactor,
            "k_CT": coef.ct_factor,
            "k_kV_CTDI": k_ctdi,
            "k_kV_organ": k_organ,
            "region": factors.region,
            "size": factors.size,
            "sex": factors.sex,
            **kv_inputs,
        },
        reference="ICRP Publication 103 DLP-to-effective-dose conversion",
    )


def effective_dose(
    coef: ScannerCoefficients,
    factors: ConversionFactors,
    *,
    dlp_air: Quantity | float | None = None,
    dlp_mgycm: Quantity | float | None = None,
    kvp: float | None = None,
    route: str | None = None,
) -> Quantity:
    """Effective dose by whichever route the scanner table prescribes.

    Args:
        route: force ``"dlpair_fmean"`` or ``"dlp_kfactor"``; by default the route
            recorded in the scanner coefficient row is used, falling back to whichever
            conversion factor is actually available.
    """
    chosen = route or coef.effective_dose_route
    if chosen == "dlpair_fmean" and factors.fmean is None:
        chosen = "dlp_kfactor"
    elif chosen == "dlp_kfactor" and factors.kfactor is None:
        chosen = "dlpair_fmean"

    if chosen == "dlpair_fmean":
        if dlp_air is None:
            raise ValueError("the DLP-in-air route needs dlp_air")
        return effective_dose_from_dlp_air(dlp_air, coef, factors, kvp=kvp)
    if dlp_mgycm is None:
        raise ValueError("the k-factor route needs dlp_mgycm")
    return effective_dose_from_dlp(dlp_mgycm, coef, factors, kvp=kvp)
