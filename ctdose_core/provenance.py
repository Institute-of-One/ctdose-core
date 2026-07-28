"""Provenance records: every number this library produces says where it came from.

The point of the package is auditability. A CTDIvol that was read from
(0018,9345) and a CTDIvol that was reconstructed from tube current and a measured
nCTDIw coefficient are *not* interchangeable, and a dose survey that mixes
them silently is not defensible. Every computed quantity is therefore returned as
a :class:`Quantity` carrying its source, the formula used, and the inputs that
went into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Source(str, Enum):
    """Where a value came from, in decreasing order of authority."""

    #: Read verbatim from a standard DICOM attribute.
    DICOM_TAG = "dicom_tag"
    #: Read from a Radiation Dose Structured Report.
    RDSR = "rdsr"
    #: Parsed out of a free-text / private attribute (e.g. GE's dose comment string).
    DICOM_TEXT = "dicom_text"
    #: Reconstructed from acquisition parameters plus a scanner coefficient table.
    PHYSICS_FALLBACK = "physics_fallback"
    #: Derived from other quantities (e.g. SSDE from CTDIvol and a conversion factor).
    DERIVED = "derived"
    #: Supplied by the caller.
    USER = "user"


@dataclass(frozen=True)
class Quantity:
    """A number with units and an audit trail."""

    value: float
    unit: str
    source: Source
    formula: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    reference: str = ""
    notes: tuple[str, ...] = ()

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"Quantity({self.value:.6g} {self.unit}, source={self.source.value})"

    @property
    def is_estimated(self) -> bool:
        """True when the value was reconstructed rather than measured/recorded."""
        return self.source in (Source.PHYSICS_FALLBACK, Source.DERIVED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "source": self.source.value,
            "formula": self.formula,
            "inputs": self.inputs,
            "reference": self.reference,
            "notes": list(self.notes),
        }


def as_float(x: Quantity | float | None) -> float | None:
    """Accept either a bare number or a :class:`Quantity`."""
    if x is None:
        return None
    return float(x)
