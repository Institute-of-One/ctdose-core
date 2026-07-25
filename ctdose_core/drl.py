"""Diagnostic reference levels and comparison against them.

The shipped table is Japan DRLs 2015 (J-RIME), extracted from the reference
implementation's own DRL assets. Additional DRL sets can be registered at runtime
with :func:`register_drl_set` -- nothing here assumes a single national table.

A DRL is a *survey* quantity: it applies to the median of a representative sample of
standard-sized patients, not to an individual scan. :func:`compare` therefore reports
the ratio and states this explicitly, rather than pronouncing a single patient
"over-exposed".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .tables import drl_tables


@dataclass(frozen=True)
class DRLEntry:
    """One diagnostic reference level."""

    examination: str
    key: str
    ctdivol_mgy: float
    dlp_mgycm: float
    phantom_cm: int
    set_name: str
    age_band: str | None = None
    age_min_years: float | None = None
    age_max_years: float | None = None
    notes: tuple[str, ...] = ()

    def applies_to_age(self, age_years: float | None) -> bool:
        if self.age_min_years is None:
            return age_years is None or age_years >= 15
        if age_years is None:
            return False
        return self.age_min_years <= age_years < (self.age_max_years or float("inf"))


_REGISTRY: dict[str, list[DRLEntry]] = {}


def _load_default() -> None:
    if _REGISTRY:
        return
    raw = drl_tables()
    name = raw["provenance"]["name"]
    entries: list[DRLEntry] = []
    for r in raw["adult"]:
        entries.append(
            DRLEntry(
                examination=r["examination"],
                key=r["key"],
                ctdivol_mgy=r["ctdivol_mgy"],
                dlp_mgycm=r["dlp_mgycm"],
                phantom_cm=r["phantom_cm"],
                set_name=name,
            )
        )
    for r in raw["pediatric"]:
        notes = (r["data_quality_note"],) if "data_quality_note" in r else ()
        entries.append(
            DRLEntry(
                examination=r["examination"],
                key=r["key"],
                ctdivol_mgy=r["ctdivol_mgy"],
                dlp_mgycm=r["dlp_mgycm"],
                phantom_cm=r["phantom_cm"],
                set_name=name,
                age_band=r["age_band"],
                age_min_years=r["age_min_years"],
                age_max_years=r["age_max_years"],
                notes=notes,
            )
        )
    _REGISTRY[name] = entries


def register_drl_set(name: str, entries: Iterable[DRLEntry]) -> None:
    """Register an additional DRL set (a newer national table, a local reference...)."""
    _REGISTRY[name] = list(entries)


def drl_sets() -> tuple[str, ...]:
    _load_default()
    return tuple(_REGISTRY)


def default_set_name() -> str:
    _load_default()
    return drl_tables()["provenance"]["name"]


def entries(set_name: str | None = None) -> tuple[DRLEntry, ...]:
    _load_default()
    return tuple(_REGISTRY[set_name or default_set_name()])


def lookup(
    key: str, *, age_years: float | None = None, set_name: str | None = None
) -> DRLEntry:
    """Find the DRL for an examination key and patient age.

    Args:
        key: examination key, e.g. ``"routine_chest"``, ``"chest"``, ``"head"``.
        age_years: patient age; selects the paediatric band where one applies.
    """
    candidates = [e for e in entries(set_name) if e.key == key and e.applies_to_age(age_years)]
    if not candidates:
        available = sorted({e.key for e in entries(set_name)})
        raise LookupError(
            f"no DRL for key={key!r} age={age_years!r}; available keys: {', '.join(available)}"
        )
    return candidates[0]


@dataclass(frozen=True)
class DRLComparison:
    """The result of comparing one scan's metrics against a DRL, with the evidence."""

    entry: DRLEntry
    metric: str
    measured: float
    reference: float
    unit: str
    ratio: float
    exceeds: bool
    measured_source: str = ""
    notes: tuple[str, ...] = ()

    @property
    def excess(self) -> float:
        """Measured minus reference, in the metric's own unit."""
        return self.measured - self.reference

    @property
    def percent_of_drl(self) -> float:
        return 100.0 * self.ratio

    def to_dict(self) -> dict[str, Any]:
        return {
            "drl_set": self.entry.set_name,
            "examination": self.entry.examination,
            "age_band": self.entry.age_band,
            "metric": self.metric,
            "measured": self.measured,
            "measured_source": self.measured_source,
            "reference": self.reference,
            "unit": self.unit,
            "ratio": self.ratio,
            "percent_of_drl": self.percent_of_drl,
            "excess": self.excess,
            "exceeds": self.exceeds,
            "phantom_cm": self.entry.phantom_cm,
            "notes": list(self.notes) + list(self.entry.notes),
        }


DISCLAIMER = (
    "A DRL applies to the median of a representative patient sample, not to an "
    "individual examination. A single scan above the DRL is a prompt to review the "
    "protocol, not evidence of an error."
)


def compare(
    entry: DRLEntry,
    *,
    ctdivol_mgy: float | None = None,
    dlp_mgycm: float | None = None,
    ctdivol_source: str = "",
    dlp_source: str = "",
) -> list[DRLComparison]:
    """Compare measured CTDIvol and/or DLP against one DRL entry."""
    out: list[DRLComparison] = []
    if ctdivol_mgy is not None:
        ratio = ctdivol_mgy / entry.ctdivol_mgy
        out.append(
            DRLComparison(
                entry=entry,
                metric="CTDIvol",
                measured=float(ctdivol_mgy),
                reference=entry.ctdivol_mgy,
                unit="mGy",
                ratio=ratio,
                exceeds=ratio > 1.0,
                measured_source=ctdivol_source,
                notes=(DISCLAIMER,),
            )
        )
    if dlp_mgycm is not None:
        ratio = dlp_mgycm / entry.dlp_mgycm
        out.append(
            DRLComparison(
                entry=entry,
                metric="DLP",
                measured=float(dlp_mgycm),
                reference=entry.dlp_mgycm,
                unit="mGy*cm",
                ratio=ratio,
                exceeds=ratio > 1.0,
                measured_source=dlp_source,
                notes=(DISCLAIMER,),
            )
        )
    return out
