"""Access to the coefficient tables shipped with the package.

The JSON files under ``ctdose_core/data`` are produced by ``tools/extract_*.py`` from
their published primary sources. Nothing in this module hard-codes a physics constant; if
a number is wrong the fix belongs in the extractor or in the published source, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache, lru_cache
from importlib import resources
from typing import Any

#: Body regions dosimetry conventionally treats with head-mode geometry.
HEAD_REGIONS = ("Head", "Neck", "Head and Neck")

#: Patient-size labels used by the published conversion-factor tables.
SIZE_ADULT = "Adults"
SIZE_CHILD_7Y = "Children7year old"
SIZE_CHILD_8W = "Children8week old"
PEDIATRIC_KFACTOR_SIZES = ("0 year old", "1 year old", "5 year old", "10 year old")


@cache
def _load(name: str) -> dict[str, Any]:
    with resources.files("ctdose_core.data").joinpath(name).open(encoding="utf-8") as fh:
        return json.load(fh)


# --- scanner coefficients ------------------------------------------------------------


class UnknownScannerError(LookupError):
    """Raised when no coefficient row covers the requested combination."""


@lru_cache(maxsize=1)
# --- DLP -> effective dose conversion factors ----------------------------------------


@dataclass(frozen=True)
class ConversionFactors:
    """DLP-to-effective-dose conversion factors for a region/size/sex combination.

    ``fmean``
        Organ-dose conversion factor applied on the DLP-in-air route (mSv per mGy*cm),
        from Deak et al. 2010, computed with **ICRP 103** tissue weighting.
    ``kfactor``
        DLP-to-effective-dose k-factor (mSv per mGy*cm), from AAPM Report No. 96 (2008)
        Table 3, which rests on **ICRP 60** tissue weighting.

    The two are not interchangeable, so a value must carry which one produced it;
    :func:`conversion_provenance` holds the citations and the weighting.
    """

    region: str
    size: str
    sex: str
    fmean: float | None = None
    kfactor: float | None = None


@lru_cache(maxsize=1)
def _conversion_records() -> tuple[ConversionFactors, ...]:
    raw = _load("conversion_factors.json")["records"]
    return tuple(
        ConversionFactors(
            region=r["region"],
            size=r["size"],
            sex=r["sex"],
            fmean=r.get("fmean"),
            kfactor=r.get("kfactor"),
        )
        for r in raw
    )


def lookup_conversion(region: str, size: str = SIZE_ADULT, sex: str = "F") -> ConversionFactors:
    """Look up ``fmean``/``kfactor``. Sex falls back to the female (conservative) row.

    The reference implementation branches on ``ptsex == 'M'`` and treats everything
    else (including empty or ``O``) as female, which yields the slightly higher
    conversion factor; that behaviour is preserved here.
    """
    sex_key = "M" if str(sex).upper().startswith("M") else "F"
    for rec in _conversion_records():
        if rec.region == region and rec.size == size and rec.sex == sex_key:
            return rec
    raise LookupError(f"no conversion factors for region={region!r} size={size!r}")


@lru_cache(maxsize=1)
def conversion_regions() -> tuple[str, ...]:
    return tuple(sorted({r.region for r in _conversion_records()}))


@lru_cache(maxsize=1)
def conversion_provenance() -> dict[str, Any]:
    """Where the conversion factors come from, and which tissue weighting they carry.

    Exposed because the two quantities in the table rest on different ICRP generations:
    ``kfactor`` on ICRP 60 via AAPM Report 96 Table 3, ``fmean`` on ICRP 103 via Deak
    et al. 2010. A reported effective dose has to be able to say which produced it, and
    a test has to be able to check that the file still says so.
    """
    return _load("conversion_factors.json")["provenance"]


# --- SSDE lookup tables ---------------------------------------------------------------


@lru_cache(maxsize=1)
def ssde_tables() -> dict[str, list[list[float]]]:
    """AAPM TG-204 lookup matrices, keyed by the reference implementation's names."""
    return _load("ssde_tables.json")["tables"]


@lru_cache(maxsize=1)
def ssde_table_quality_findings() -> tuple[dict[str, Any], ...]:
    """Rows of the measured-dimension tables that disagree with the effective-diameter
    tables. Reported, not corrected -- see the README's data-quality section."""
    return tuple(_load("ssde_tables.json").get("quality_findings", ()))


@lru_cache(maxsize=1)
def age_to_effective_diameter() -> tuple[tuple[float, float], ...]:
    """Nominal patient age (years) -> effective diameter (cm)."""
    rows = _load("ssde_tables.json")["age_to_effective_diameter"]
    return tuple((r["age_years"], r["effective_diameter_cm"]) for r in rows)


# --- DRL tables ------------------------------------------------------------------------


@lru_cache(maxsize=1)
def drl_tables() -> dict[str, Any]:
    return _load("drl_tables.json")
