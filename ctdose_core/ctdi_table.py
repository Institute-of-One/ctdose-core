"""Look up a scanner's normalised CTDIw in the open MIRDct database.

The table (Dinwiddie et al. 2026, CC BY 4.0 -- see
``data/reference/mirdct_dinwiddie2026/SOURCE.md``) gives **CTDIw in mGy per 100 mAs at
pitch 1.0**, keyed on scanner model, tube potential, beam collimation, CTDI phantom and
bowtie filter. This module resolves an acquisition against that grid.

Two things it deliberately does not do:

* **It does not extrapolate.** Outside the tabulated tube-potential range for a model the
  lookup fails rather than guessing; the series is then unrecoverable, which is a
  reportable outcome and not an error.
* **It does not snap silently.** Where the exact technique is not tabulated the value is
  interpolated over kV and matched to the nearest collimation, and every such step is
  recorded on the result so it reaches the value's provenance and the released data.

Replacing the previous CT-Expo-derived coefficients removes two modelling assumptions.
Collimation is tabulated here rather than corrected for with an over-beaming factor, and
tube potential is tabulated rather than scaled by ``(kV/kV_ref)^2``.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data" / "scanner_ctdi_mirdct.json"

#: Bowtie labels the table uses, widest last.
BOWTIES = ("small", "medium", "large")


class CoefficientNotAvailable(LookupError):
    """No row in the open database covers this scanner or technique.

    Raised rather than approximated: a series the open table cannot cover is
    *unrecoverable* by the reconstruction, which the survey counts explicitly.
    """


@dataclass(frozen=True)
class CtdiLookup:
    """One resolved CTDIw value, with everything that was assumed to reach it."""

    nctdi_w_mgy_per_100mas: float
    model: str
    vendor: str
    phantom_cm: int
    bowtie: str
    kvp: float
    collimation_mm: float
    #: Human-readable notes for anything that was not an exact match.
    assumptions: tuple[str, ...] = ()

    @property
    def exact(self) -> bool:
        return not self.assumptions


@dataclass(frozen=True)
class TableModel:
    """A model in the open database and the technique grid it was measured on."""

    vendor: str
    model: str
    rows: tuple[dict[str, Any], ...] = field(repr=False, default=())

    def kvp_values(self, phantom_cm: int, bowtie: str, collimation_mm: float) -> list[float]:
        return sorted(
            {
                r["kvp"]
                for r in self.rows
                if r["phantom_cm"] == phantom_cm
                and r["bowtie"] == bowtie
                and r["collimation_mm"] == collimation_mm
            }
        )


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    if not DATA.is_file():
        raise CoefficientNotAvailable(
            f"the open coefficient database is missing: {DATA}. "
            "Run tools/extract_mirdct_coefficients.py."
        )
    return json.loads(DATA.read_text(encoding="utf-8"))


def provenance() -> dict[str, Any]:
    """Citation and licence of the shipped coefficients."""
    return _payload()["provenance"]


@lru_cache(maxsize=1)
def models() -> tuple[TableModel, ...]:
    rows = _payload()["rows"]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["vendor"], row["model"]), []).append(row)
    return tuple(
        TableModel(vendor=v, model=m, rows=tuple(rs)) for (v, m), rs in sorted(grouped.items())
    )


def model_names() -> tuple[tuple[str, str], ...]:
    """``(vendor, model)`` for every scanner the open database covers."""
    return tuple((m.vendor, m.model) for m in models())


def _nearest(value: float, candidates: list[float]) -> float:
    return min(candidates, key=lambda c: (abs(c - value), c))


def _interpolate_kv(kvp: float, grid: list[float], value_at) -> tuple[float, str | None]:
    """Value at ``kvp``, interpolating between bracketing tabulated potentials.

    CTDI output rises steeply and smoothly with tube potential, so a log-log
    interpolation between the two bracketing measured points is a far better estimate
    than the nearest one. Outside the measured range we refuse rather than extrapolate.
    """
    if kvp in grid:
        return value_at(kvp), None
    if kvp < grid[0] or kvp > grid[-1]:
        raise CoefficientNotAvailable(
            f"{kvp:.0f} kVp is outside the tabulated range {grid[0]:.0f}-{grid[-1]:.0f} kVp; "
            "the open database does not cover this technique and extrapolating tube "
            "output beyond measured potentials is not defensible"
        )
    i = bisect_left(grid, kvp)
    lo, hi = grid[i - 1], grid[i]
    v_lo, v_hi = value_at(lo), value_at(hi)

    import math

    # log-log in kV: output follows a power law in tube potential closely enough over a
    # 10-20 kV bracket that this is materially better than linear interpolation.
    exponent = math.log(v_hi / v_lo) / math.log(hi / lo)
    value = v_lo * (kvp / lo) ** exponent
    note = (
        f"tube potential {kvp:.0f} kVp is not tabulated; interpolated between "
        f"{lo:.0f} and {hi:.0f} kVp (power law, exponent {exponent:.2f})"
    )
    return value, note


def lookup(
    vendor: str,
    model: str,
    *,
    kvp: float,
    collimation_mm: float,
    phantom_cm: int,
    bowtie: str | None = None,
) -> CtdiLookup:
    """Resolve CTDIw in mGy per 100 mAs for one acquisition.

    Args:
        bowtie: ``small``/``medium``/``large``. When omitted the table's own choice for
            the phantom is used -- small for the 16 cm head phantom, medium for the 32 cm
            body phantom -- and that substitution is recorded as an assumption.

    Raises:
        CoefficientNotAvailable: the model, phantom or technique is not covered.
    """
    match = next(
        (m for m in models() if m.vendor == vendor and m.model == model),
        None,
    )
    if match is None:
        raise CoefficientNotAvailable(
            f"{vendor} {model!r} is not in the open coefficient database "
            f"({len(models())} models); this series is unrecoverable by reconstruction"
        )

    rows = [r for r in match.rows if r["phantom_cm"] == phantom_cm]
    if not rows:
        raise CoefficientNotAvailable(
            f"{model!r} has no {phantom_cm} cm phantom rows in the open database"
        )

    assumptions: list[str] = []

    # The grid is ragged: several models were measured with only one or two bowties for a
    # given phantom (Definition Flash at 16 cm has only the large filter). Resolve against
    # what exists rather than against what is conventional, and describe what happened
    # rather than what was intended.
    available_bowties = sorted({r["bowtie"] for r in rows}, key=BOWTIES.index)
    conventional = "small" if phantom_cm == 16 else "medium"
    if bowtie is None:
        if conventional in available_bowties:
            chosen = conventional
            assumptions.append(
                f"bowtie not recorded in DICOM; used the {chosen!r} filter, the usual "
                f"choice for the {phantom_cm} cm phantom"
            )
        else:
            chosen = _nearest_bowtie(conventional, available_bowties)
            assumptions.append(
                f"bowtie not recorded in DICOM; the {conventional!r} filter usual for the "
                f"{phantom_cm} cm phantom is not tabulated for this model, so {chosen!r} "
                f"was used (available: {', '.join(available_bowties)})"
            )
    elif bowtie in available_bowties:
        chosen = bowtie
    else:
        chosen = _nearest_bowtie(bowtie, available_bowties)
        assumptions.append(
            f"{bowtie!r} bowtie is not tabulated for this model; used {chosen!r} "
            f"(available: {', '.join(available_bowties)})"
        )
    rows = [r for r in rows if r["bowtie"] == chosen]

    collimations = sorted({r["collimation_mm"] for r in rows})
    coll = _nearest(collimation_mm, collimations)
    if abs(coll - collimation_mm) > 1e-9:
        assumptions.append(
            f"collimation {collimation_mm:.1f} mm is not tabulated; used the nearest "
            f"measured value, {coll:.1f} mm (tabulated: "
            f"{', '.join(f'{c:g}' for c in collimations)})"
        )
    rows = [r for r in rows if r["collimation_mm"] == coll]

    grid = sorted(r["kvp"] for r in rows)
    by_kv = {r["kvp"]: r["nctdi_w_mgy_per_100mas"] for r in rows}
    value, note = _interpolate_kv(kvp, grid, by_kv.__getitem__)
    if note:
        assumptions.append(note)

    return CtdiLookup(
        nctdi_w_mgy_per_100mas=value,
        model=match.model,
        vendor=match.vendor,
        phantom_cm=phantom_cm,
        bowtie=chosen,
        kvp=kvp,
        collimation_mm=coll,
        assumptions=tuple(assumptions),
    )


def _nearest_bowtie(requested: str, available: list[str]) -> str:
    if requested not in BOWTIES:
        return available[0]
    want = BOWTIES.index(requested)
    return min(available, key=lambda b: abs(BOWTIES.index(b) - want))
