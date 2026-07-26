"""Matching a DICOM ``Manufacturer's Model Name`` to a coefficient-table entry.

DICOM records a marketing model name (``LightSpeed VCT``); the coefficient table is
keyed by scanner *series and bowtie/scan-field configuration*
(``GE LightSpeed VCT (small BS)``). The mapping is therefore one-to-many and cannot
be resolved from the header alone, so this module returns candidates and makes the
caller's choice explicit rather than guessing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tables import scanner_models

#: Bowtie / scan-field-size qualifiers used in the table's model names.
BOWTIE_QUALIFIERS = ("small", "medium", "large")

_MANUFACTURER_PREFIXES = {
    "ge": "GE",
    "ge medical systems": "GE",
    "gems": "GE",
    "toshiba": "Canon/Toshiba",
    "toshiba medical systems": "Canon/Toshiba",
    "canon medical systems": "Canon/Toshiba",
    "siemens": "Siemens",
    "siemens healthineers": "Siemens",
    "philips": "Philips",
    "philips medical systems": "Philips",
}

#: Which vendor each coefficient-table model name belongs to, keyed by name prefix.
#: Used to forbid cross-vendor matches outright: a Philips scanner must never be costed
#: against a GE coefficient row, however many tokens the two names happen to share
#: (``Mx8000 IDT 16`` and ``GE LightSpeed 16`` share "16").
_TABLE_MODEL_VENDORS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("GE",), "GE"),
    (("Aquilion", "Alexion"), "Canon/Toshiba"),
    (("Definition", "Sensation", "Perspective", "Volume Zoom"), "Siemens"),
    (("Brilliance", "Ingenuity"), "Philips"),
)


def table_model_vendor(table_model: str) -> str:
    for prefixes, vendor in _TABLE_MODEL_VENDORS:
        if table_model.startswith(prefixes):
            return vendor
    return ""


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _normalise(text).split() if t not in ("ct", "series", "bs")}


@dataclass(frozen=True)
class ModelMatch:
    """One candidate coefficient-table model name, with its match quality."""

    table_model: str
    score: float
    bowtie: str | None = None

    @property
    def is_ambiguous_bowtie(self) -> bool:
        return self.bowtie is not None


def _bowtie_of(table_model: str) -> str | None:
    m = re.search(r"\((small|medium|large) BS\)", table_model)
    return m.group(1) if m else None


def match_models(model_name: str, manufacturer: str = "") -> list[ModelMatch]:
    """Rank coefficient-table model names against a DICOM model name.

    Scoring is token overlap (Jaccard), with a small bonus when the manufacturer agrees.
    When the manufacturer is recognised, rows belonging to a *different* vendor are
    excluded outright rather than merely scored down. Returns every remaining candidate
    that shares at least one token, best first.
    """
    want = _tokens(model_name)
    if not want:
        return []
    vendor = _MANUFACTURER_PREFIXES.get(_normalise(manufacturer), "")
    out: list[ModelMatch] = []
    for table_model in scanner_models():
        row_vendor = table_model_vendor(table_model)
        if vendor and row_vendor and row_vendor != vendor:
            continue
        have = _tokens(table_model)
        overlap = want & have
        if not overlap:
            continue
        score = len(overlap) / len(want | have)
        if vendor and row_vendor == vendor:
            score += 0.05
        out.append(ModelMatch(table_model, score, _bowtie_of(table_model)))
    out.sort(key=lambda m: (-m.score, m.table_model))
    return out


class AmbiguousScannerError(LookupError):
    """Raised when a DICOM model name maps to several table entries."""

    def __init__(self, model_name: str, candidates: list[ModelMatch]):
        self.candidates = candidates
        super().__init__(
            f"{model_name!r} matches {len(candidates)} coefficient-table entries: "
            + ", ".join(repr(c.table_model) for c in candidates)
            + ". Pass scanner_model= (or bowtie=) to choose."
        )


#: Minimum token-overlap score for a match to be accepted.
#:
#: Calibrated against the 40 distinct DICOM model names found in a stratified sample of
#: public CT (see ``results/survey.csv``): every correct match there scores >= 0.45
#: (``Sensation 64`` -> ``Sensation 40/64``, 0.72; ``Aquilion PRIME`` ->
#: ``Aquilion Prime``, 0.72), while every spurious one scores <= 0.38
#: (``Aquilion`` -> ``Aquilion Prime``, 0.38, wrong family; ``SOMATOM Definition Flash``
#: -> ``Definition Edge``, 0.30, a model the table does not contain). Reporting a model
#: as unknown is preferable to costing it against the wrong coefficient row.
MIN_MATCH_SCORE = 0.45


def resolve_model(
    model_name: str,
    manufacturer: str = "",
    *,
    bowtie: str | None = None,
    min_score: float = MIN_MATCH_SCORE,
) -> tuple[str, list[str]]:
    """Resolve a DICOM model name to exactly one coefficient-table model name.

    Args:
        bowtie: ``"small"``, ``"medium"`` or ``"large"`` to disambiguate scanners
            whose coefficients depend on the bowtie / scan field of view.

    Returns:
        ``(table_model, warnings)``.

    Raises:
        LookupError: when nothing matches well enough.
        AmbiguousScannerError: when several entries tie and ``bowtie`` does not resolve it.
    """
    matches = [m for m in match_models(model_name, manufacturer) if m.score >= min_score]
    if not matches:
        raise LookupError(
            f"no coefficient-table entry resembles model {model_name!r} "
            f"(manufacturer {manufacturer!r}); known models: {', '.join(scanner_models())}"
        )
    warnings: list[str] = []
    if bowtie:
        if bowtie not in BOWTIE_QUALIFIERS:
            raise ValueError(f"bowtie must be one of {BOWTIE_QUALIFIERS}, got {bowtie!r}")
        narrowed = [m for m in matches if m.bowtie == bowtie]
        if narrowed:
            matches = narrowed

    best = matches[0]
    tied = [m for m in matches if abs(m.score - best.score) < 1e-9]
    if len(tied) > 1:
        raise AmbiguousScannerError(model_name, tied)
    if best.score < 0.6:
        warnings.append(
            f"scanner model {model_name!r} matched {best.table_model!r} with low "
            f"confidence ({best.score:.2f}); verify the coefficients apply"
        )
    return best.table_model, warnings
