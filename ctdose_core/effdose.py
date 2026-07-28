"""Effective dose, by the two routes the reference implementation supports.

**DLP-in-air route** (adults and the two tabulated paediatric phantoms)::

    E = DLP_air * f_mean * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5

**ICRP-103 k-factor route** (the 0/1/5/10-year-old paediatric phantoms)::

    E = DLP * k_CT * (kV/kV_ref)^2 * (kV/kV_ref)^0.5 * k

Both are region- and sex-dependent through the shipped conversion-factor table.
"""

from __future__ import annotations

from .provenance import Quantity, Source
from .tables import ConversionFactors


def effective_dose(
    dlp_mgycm: Quantity | float,
    factors: ConversionFactors,
) -> Quantity:
    """``E = DLP * k(region, size)`` -- the published k-factor route.

    The conversion factor depends on the examined region and the patient size, not on the
    scanner, so this needs no per-scanner data. The previous implementation additionally
    applied a per-scanner ``k_CT`` and two tube-voltage terms tied to a per-scanner
    reference voltage; those are gone with the coefficient table they came from.

    Effective dose obtained this way is a population quantity for a reference phantom, not
    an individual patient's dose. Where the DLP was itself reconstructed it inherits that
    DLP's caveats, which travel on the value rather than in prose.
    """
    if factors.kfactor is None:
        raise ValueError(
            f"no k-factor for region={factors.region!r} size={factors.size!r}"
        )
    d = float(dlp_mgycm)
    dlp_source = dlp_mgycm.source if isinstance(dlp_mgycm, Quantity) else Source.USER
    notes = [
        "a population estimate for a reference phantom, not an individual patient's dose"
    ]
    if isinstance(dlp_mgycm, Quantity):
        notes.extend(n for n in dlp_mgycm.notes if "over-ranging" in n)
    return Quantity(
        value=d * factors.kfactor,
        unit="mSv",
        source=Source.DERIVED,
        formula="E = DLP * k(region, size)",
        inputs={
            "DLP_mGycm": d,
            "DLP_source": dlp_source.value,
            "k": factors.kfactor,
            "region": factors.region,
            "size": factors.size,
            "sex": factors.sex,
        },
        notes=tuple(notes),
        reference="ICRP Publication 103 (2007) DLP-to-effective-dose conversion",
    )
