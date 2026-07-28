"""Access to the coefficient tables shipped with the package.

The JSON files under ``ctdose_core/data`` are produced by ``tools/extract_*.py``
from their published primary sources. Nothing in this module
hard-codes a physics constant; if a number is wrong the fix belongs in the
extractor or in the MATLAB, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache, lru_cache
from importlib import resources
from typing import Any

#: Body regions the reference implementation treats with head-mode geometry.
HEAD_REGIONS = ("Head", "Neck", "Head and Neck")

#: Patient-size labels used by the reference implementation.
SIZE_ADULT = "Adults"
SIZE_CHILD_7Y = "Children7year old"
SIZE_CHILD_8W = "Children8week old"
PEDIATRIC_KFACTOR_SIZES = ("0 year old", "1 year old", "5 year old", "10 year old")


@cache
def _load(name: str) -> dict[str, Any]:
    with resources.files("ctdose_core.data").joinpath(name).open(encoding="utf-8") as fh:
        return json.load(fh)


# --- scanner coefficients ------------------------------------------------------------


@dataclass(frozen=True)
class ScannerCoefficients:
    """Physics constants for one (scanner, region, patient-size) combination.

    Attributes follow the CT-Expo formulation (Stamm & Nagel, Rofo 2002):

    ``nctdi_air``
        Normalised free-in-air CTDI, mGy/mAs, for the scanner's head or body mode.
    ``nctdi_w``
        Normalised weighted CTDI in the reference phantom, mGy/mAs. Used to rebuild
        CTDIvol when the DICOM CTDIvol attribute is absent.
    ``ct_factor``
        Scanner/mode correction factor applied on the effective-dose route.
    ``beam_width_ref_mm`` / ``overbeam_dz_mm``
        Reference nominal beam width N and penumbra dz for the over-beaming factor.
    ``overrange_m`` / ``overrange_b``
        Slope and intercept of the over-ranging length model.
    ``kv_reference``
        Tube voltage the scanner's coefficients were normalised at (kV).
    ``effective_dose_route``
        ``"dlpair_fmean"`` (DLP-in-air x organ conversion) or ``"dlp_kfactor"``
        (DLP x ICRP-103 k-factor), as chosen by the reference implementation.
    """

    models: tuple[str, ...]
    regions: tuple[str, ...] | None
    sizes: tuple[str, ...] | None
    available: bool
    nctdi_air: float | None = None
    nctdi_air_expr: str | None = None
    nctdi_w: float | None = None
    ct_factor: float | None = None
    beam_width_ref_mm: float | None = None
    overbeam_dz_mm: float | None = None
    overrange_m: float | None = None
    overrange_b: float | None = None
    kv_reference: float = 120.0
    effective_dose_route: str = "dlpair_fmean"
    #: Data-quality problems found in the reference implementation for this row.
    #: Values are never silently corrected; they are reported here and surfaced as
    #: warnings by the pipeline.
    quality_flags: tuple[str, ...] = ()
    ct_factor_inherited_from: str | None = None

    @property
    def is_head_mode(self) -> bool:
        return self.regions is not None and set(self.regions) == set(HEAD_REGIONS)


class UnknownScannerError(LookupError):
    """Raised when no coefficient row covers the requested combination."""


@lru_cache(maxsize=1)
def _scanner_records() -> tuple[ScannerCoefficients, ...]:
    raw = _load("scanner_coefficients.json")["records"]
    out = []
    for r in raw:
        out.append(
            ScannerCoefficients(
                models=tuple(r["models"]),
                regions=tuple(r["regions"]) if r.get("regions") else None,
                sizes=tuple(r["sizes"]) if r.get("sizes") else None,
                available=r["available"],
                nctdi_air=r.get("nctdi_air"),
                nctdi_air_expr=r.get("nctdi_air_expr"),
                nctdi_w=r.get("nctdi_w"),
                ct_factor=r.get("ct_factor"),
                beam_width_ref_mm=r.get("beam_width_ref_mm"),
                overbeam_dz_mm=r.get("overbeam_dz_mm"),
                overrange_m=r.get("overrange_m"),
                overrange_b=r.get("overrange_b"),
                kv_reference=r.get("kv_reference", 120.0),
                effective_dose_route=r.get("effective_dose_route", "dlpair_fmean"),
                quality_flags=tuple(r.get("quality_flags", ())),
                ct_factor_inherited_from=r.get("ct_factor_inherited_from"),
            )
        )
    return tuple(out)


@lru_cache(maxsize=1)
def scanner_models() -> tuple[str, ...]:
    """Every scanner model name the coefficient table knows about."""
    names: set[str] = set()
    for rec in _scanner_records():
        names.update(rec.models)
    return tuple(sorted(names))


def _size_matches(rec: ScannerCoefficients, size: str) -> int:
    """Match score for the patient-size axis; -1 means no match."""
    if rec.sizes is None:
        return 0  # the MATLAB ``otherwise`` branch: matches anything
    return 2 if size in rec.sizes else -1


def _region_matches(rec: ScannerCoefficients, region: str) -> int:
    if rec.regions is None:
        return 0  # ``otherwise``: the body/trunk branch
    return 2 if region in rec.regions else -1


def lookup_scanner(model: str, region: str, size: str = SIZE_ADULT) -> ScannerCoefficients:
    """Resolve the coefficient row for a scanner model, body region and patient size.

    Matching follows the reference implementation's ``switch`` semantics: an explicit
    ``case`` label beats the ``otherwise`` branch on each of the region and size axes.

    Raises:
        UnknownScannerError: if the model is unknown, or the reference implementation
            has no data for that combination (its ``No Data`` branches).
    """
    candidates = [r for r in _scanner_records() if model in r.models]
    if not candidates:
        raise UnknownScannerError(
            f"scanner model {model!r} is not in the coefficient table; "
            f"known models: {', '.join(scanner_models())}"
        )
    scored = []
    for rec in candidates:
        rs, ss = _region_matches(rec, region), _size_matches(rec, size)
        if rs < 0 or ss < 0:
            continue
        scored.append((rs + ss, rec))
    if not scored:
        raise UnknownScannerError(
            f"no coefficients for model={model!r} region={region!r} size={size!r}"
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    best = scored[0][1]
    if not best.available:
        raise UnknownScannerError(
            f"the reference implementation has no data for model={model!r} "
            f"region={region!r} size={size!r} (its 'No Data' branch)"
        )
    return best


# --- DLP -> effective dose conversion factors ----------------------------------------


@dataclass(frozen=True)
class ConversionFactors:
    """DLP-to-effective-dose conversion factors for a region/size/sex combination.

    ``fmean``
        Organ-dose conversion factor applied on the DLP-in-air route (mSv per mGy*cm).
    ``kfactor``
        ICRP-103 DLP-to-effective-dose k-factor (mSv per mGy*cm).
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
