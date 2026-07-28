"""CTDIvol, DLP and effective dose, reconstructed when the dose attributes are absent.

Public CT collections frequently lack CTDIvol (0018,9345) and any usable DLP record while
retaining the full acquisition description, including the per-slice tube current. Two
routes reach a value:

* **recorded** -- read it from the RDSR or the DICOM attribute;
* **reconstructed** -- rebuild CTDIvol from the acquisition and an openly licensed
  coefficient table, then DLP from the imaged length and effective dose from a published
  conversion factor.

Which route was taken, and every assumption the reconstruction rested on, is visible in
the returned :class:`Quantity`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .ctdi_table import CoefficientNotAvailable
from .io import ScanSeries
from .provenance import Quantity, Source


@dataclass(frozen=True)
class Acquisition:
    """The acquisition geometry a dose calculation needs, resolved and validated.

    Built by :func:`acquisition_from_series`, or directly by the caller when working
    from a dose report rather than image headers.
    """

    mas_per_rotation: float
    collimation_mm: float
    pitch: float
    slice_thickness_mm: float
    scan_range_mm: float
    kvp: float | None = None

    def __post_init__(self) -> None:
        for name in ("mas_per_rotation", "collimation_mm", "pitch", "slice_thickness_mm"):
            if getattr(self, name) is None or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)!r}")
        if self.scan_range_mm < 0:
            raise ValueError("scan_range_mm must not be negative")


def acquisition_from_series(series: ScanSeries) -> Acquisition:
    """Collect the geometry needed for dosimetry, raising if something essential is missing."""
    missing = []
    if series.tube_current_ma.size == 0:
        missing.append("tube current (0018,1151)")
    if series.rotation_time_s is None or not series.rotation_time_s.value:
        missing.append("rotation/exposure time")
    if series.collimation_mm is None or not series.collimation_mm.value:
        missing.append("total collimation")
    if not series.slice_thickness_mm:
        missing.append("slice thickness (0018,0050)")
    if missing:
        raise ValueError("cannot build an Acquisition; missing: " + ", ".join(missing))
    return Acquisition(
        mas_per_rotation=series.mas_per_rotation,
        collimation_mm=float(series.collimation_mm.value),
        pitch=float(series.pitch.value) if series.pitch and series.pitch.value else 1.0,
        slice_thickness_mm=float(series.slice_thickness_mm),
        scan_range_mm=series.scan_range_mm,
        kvp=series.kvp,
    )


def estimate_ctdivol_open(
    acq: Acquisition,
    *,
    vendor: str,
    model: str,
    phantom_cm: int,
    bowtie: str | None = None,
) -> Quantity:
    """Rebuild CTDIvol from the open MIRDct coefficient database.

    ``CTDIvol = CTDIw(model, kVp, collimation, phantom, bowtie) * (mAs_rot / 100) / pitch``

    The table (Dinwiddie et al. 2026, CC BY 4.0) gives **CTDIw in mGy per 100 mAs at pitch
    1.0**. CTDIw is pitch-independent and ``CTDIvol = CTDIw / pitch``, so the pitch division
    is applied here rather than being folded into the tabulated value.

    Two corrections the previous route needed are absent by construction, because the
    quantities they approximated are tabulated directly:

    * no over-beaming factor -- collimation is a lookup key, so the penumbra contribution
      is measured rather than modelled from a single reference beam width;
    * no ``(kV/kV_ref)^2`` scaling -- tube potential is a lookup key.

    Raises:
        CoefficientNotAvailable: the scanner or technique is outside the open database.
            The caller should report the series as *unrecoverable* rather than substitute
            a similar scanner.
    """
    from .ctdi_table import lookup, provenance

    if acq.kvp is None:
        raise CoefficientNotAvailable(
            "tube voltage (0018,0060) is required to use the open coefficient database, "
            "which is tabulated by tube potential"
        )

    hit = lookup(
        vendor,
        model,
        kvp=float(acq.kvp),
        collimation_mm=float(acq.collimation_mm),
        phantom_cm=phantom_cm,
        bowtie=bowtie,
    )
    value = hit.nctdi_w_mgy_per_100mas * (acq.mas_per_rotation / 100.0) / acq.pitch

    notes = ["reconstructed because CTDIvol (0018,9345) was not available"]
    notes.extend(hit.assumptions)
    if hit.exact:
        notes.append(
            "every lookup key matched a measured row exactly; no interpolation or "
            "substitution was required"
        )

    return Quantity(
        value=value,
        unit="mGy",
        source=Source.PHYSICS_FALLBACK,
        formula="CTDIvol = CTDIw_table * (mAs_rot / 100) / pitch",
        inputs={
            "CTDIw_mGy_per_100mAs": hit.nctdi_w_mgy_per_100mas,
            "mAs_rot": acq.mas_per_rotation,
            "pitch": acq.pitch,
            "table_model": f"{hit.vendor} {hit.model}",
            "table_kvp": hit.kvp,
            "table_collimation_mm": hit.collimation_mm,
            "table_phantom_cm": hit.phantom_cm,
            "table_bowtie": hit.bowtie,
        },
        reference=provenance()["citation"],
        notes=tuple(notes),
    )


def estimate_dlp_open(
    ctdivol_mgy: Quantity | float, scan_length_mm: float
) -> Quantity:
    """``DLP = CTDIvol * L / 10``, with ``L`` the imaged scan length in mm.

    The imaged length is what the reconstructed slice positions span. It **omits helical
    over-ranging**, the extra rotations at each end that irradiate beyond the reconstructed
    volume, because the per-scanner over-ranging coefficients that quantify it are not
    available from an openly licensed source.

    The result is therefore a **conservative underestimate**: the true DLP is larger by the
    over-ranging contribution, typically a few centimetres of additional irradiated length.
    That is stated in the value's notes so it can never be read as an exact figure, and it
    is a substitution point -- published generic over-ranging coefficients would slot in
    here without changing anything else.
    """
    c = float(ctdivol_mgy)
    length_cm = scan_length_mm / 10.0
    value = c * length_cm
    ctdivol_source = (
        ctdivol_mgy.source if isinstance(ctdivol_mgy, Quantity) else Source.USER
    )
    return Quantity(
        value=value,
        unit="mGy*cm",
        source=Source.DERIVED,
        formula="DLP = CTDIvol * L_imaged / 10",
        inputs={
            "CTDIvol_mGy": c,
            "CTDIvol_source": ctdivol_source.value,
            "L_imaged_mm": scan_length_mm,
        },
        notes=(
            "the imaged scan length omits helical over-ranging, so this DLP is a "
            "conservative underestimate of the delivered dose-length product",
        ),
        reference="DLP definition, IEC 60601-2-44",
    )


def effective_dose_open(
    dlp_mgycm: Quantity | float, kfactor: float, *, region: str, size: str, sex: str
) -> Quantity:
    """``E = DLP * k``, with ``k`` a published region- and age-specific conversion factor.

    The factor depends on the examined region and patient size only, not on the scanner,
    so this route needs no per-scanner data. Effective dose so obtained is a population
    quantity for a reference phantom, not an individual patient's dose, and it inherits
    the DLP's over-ranging underestimate.
    """
    if kfactor is None:
        raise ValueError(f"no k-factor for region={region!r} size={size!r}")
    d = float(dlp_mgycm)
    dlp_source = dlp_mgycm.source if isinstance(dlp_mgycm, Quantity) else Source.USER
    notes = [
        "a population estimate for a reference phantom, not an individual patient's dose"
    ]
    if isinstance(dlp_mgycm, Quantity):
        notes.extend(n for n in dlp_mgycm.notes if "over-ranging" in n)
    return Quantity(
        value=d * kfactor,
        unit="mSv",
        source=Source.DERIVED,
        formula="E = DLP * k(region, size)",
        inputs={
            "DLP_mGycm": d,
            "DLP_source": dlp_source.value,
            "k": kfactor,
            "region": region,
            "size": size,
            "sex": sex,
        },
        notes=tuple(notes),
        reference="ICRP Publication 103 (2007) DLP-to-effective-dose conversion",
    )


#: Where a recorded dose value may come from, most authoritative first. The RDSR is the
#: scanner's own dose record for the irradiation event and outranks the image header;
#: the image header outranks free-text dose comments; a physics reconstruction is used
#: only when none of them is available.
SOURCE_PRIORITY: tuple[Source, ...] = (
    Source.RDSR,
    Source.DICOM_TAG,
    Source.DICOM_TEXT,
    Source.USER,
    Source.PHYSICS_FALLBACK,
)


def most_authoritative(candidates: Iterable[Quantity]) -> Quantity | None:
    """Pick the recorded value with the highest-authority source.

    Args:
        candidates: recorded values for the same quantity from different origins.
    """
    ranked = sorted(
        (q for q in candidates if q is not None),
        key=lambda q: SOURCE_PRIORITY.index(q.source)
        if q.source in SOURCE_PRIORITY
        else len(SOURCE_PRIORITY),
    )
    return ranked[0] if ranked else None


def agreement(a: Quantity, b: Quantity) -> dict[str, Any]:
    """Compare two independent records of the same quantity.

    Used when an RDSR and the image header both carry a dose index: rather than
    discarding the lower-authority one, both are kept and their difference reported.
    A disagreement usually means the SR event was matched to the wrong series.
    """
    va, vb = float(a), float(b)
    denominator = abs(va) if va else None
    return {
        "a": {"value": va, "source": a.source.value, "unit": a.unit},
        "b": {"value": vb, "source": b.source.value, "unit": b.unit},
        "absolute_difference": vb - va,
        "relative_difference": (vb - va) / denominator if denominator else None,
        "percent_difference": 100.0 * (vb - va) / denominator if denominator else None,
    }


def fallback_error(recorded: float, estimated: float) -> dict[str, Any]:
    """Signed relative error of a fallback estimate against a recorded value.

    Used by the validation study: for series that *do* carry CTDIvol, the fallback is
    run anyway and compared, which quantifies how much to trust it where the tag is
    missing.
    """
    if recorded == 0:
        raise ValueError("recorded value is zero; relative error is undefined")
    diff = estimated - recorded
    return {
        "recorded": recorded,
        "estimated": estimated,
        "absolute_error": diff,
        "relative_error": diff / recorded,
        "percent_error": 100.0 * diff / recorded,
    }
