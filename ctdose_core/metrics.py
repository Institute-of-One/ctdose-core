"""CTDIvol, DLP and DLP-in-air, with a physics fallback when the tags are absent.

Public CT collections frequently lack CTDIvol (0018,9345) and any usable DLP
record while retaining full acquisition parameters including per-slice tube
current. This module reproduces both routes:

* **recorded** -- read the value from the DICOM attribute;
* **fallback** -- rebuild it from mAs, collimation and pitch using the scanner's
  CT-Expo style normalised CTDI coefficients.

Which route was taken is always visible in the returned :class:`Quantity`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .ctdi_table import CoefficientNotAvailable
from .geometry import overbeaming_factor, overranging_length
from .io import ScanSeries
from .provenance import Quantity, Source
from .tables import ScannerCoefficients


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


def _geometry(acq: Acquisition, coef: ScannerCoefficients) -> tuple[Quantity, Quantity]:
    k_ob = overbeaming_factor(
        acq.collimation_mm, coef.beam_width_ref_mm, coef.overbeam_dz_mm
    )
    l_rot = overranging_length(
        acq.slice_thickness_mm, acq.collimation_mm, acq.pitch, coef.overrange_m, coef.overrange_b
    )
    return k_ob, l_rot


def estimate_ctdivol(
    acq: Acquisition, coef: ScannerCoefficients, *, kv_correction: bool = True
) -> Quantity:
    """Rebuild CTDIvol from acquisition parameters (the tag-absent route).

    ``CTDIvol = nCTDIw * (mA * t_rot) * k_OB / pitch * (kVp / kV_ref)^2``

    ``nCTDIw`` is the scanner's normalised weighted CTDI in the relevant reference
    phantom (16 cm head / 32 cm body), in mGy per mAs, tabulated at the scanner's
    reference tube voltage ``kV_ref`` (typically 120 kVp).

    Args:
        kv_correction: when ``True`` (default), scale the tabulated nCTDIw from its
            reference voltage to the acquisition's tube voltage by ``(kVp/kV_ref)^2``.
            This is required for a scanner-model-neutral reconstruction to match the
            as-delivered CTDIvol on non-reference-kVp scans (validated against a
            vendor Source-of-Truth: a 100 kVp abdomen scan is otherwise overestimated
            by ~46 %). Set ``False`` to reproduce the reference implementation's
            uncorrected behaviour exactly (e.g. for MATLAB-parity checks); at the
            reference voltage the two are identical.

    Note:
        The exponent 2 follows the reference implementation's ``kvfu1`` free-in-air
        CTDI scaling and is the same factor used on the effective-dose route
        (:func:`~ctdose_core.geometry.kv_corrections`). It is applied only when the
        acquisition's ``kvp`` is known and differs from ``kV_ref``.
    """
    k_ob, _ = _geometry(acq, coef)
    value = coef.nctdi_w * acq.mas_per_rotation * float(k_ob) / acq.pitch

    inputs = {
        "nCTDIw_mGy_per_mAs": coef.nctdi_w,
        "mAs_rot": acq.mas_per_rotation,
        "k_OB": float(k_ob),
        "pitch": acq.pitch,
    }
    formula = "CTDIvol = nCTDIw * mAs_rot * k_OB / pitch"
    notes = ["reconstructed because CTDIvol (0018,9345) was not available"]

    apply_kv = (
        kv_correction
        and acq.kvp is not None
        and coef.kv_reference
        and float(acq.kvp) != float(coef.kv_reference)
    )
    if apply_kv:
        k_kv = (float(acq.kvp) / float(coef.kv_reference)) ** 2
        value *= k_kv
        inputs["kVp"] = float(acq.kvp)
        inputs["kV_ref"] = float(coef.kv_reference)
        inputs["k_kV"] = k_kv
        formula = "CTDIvol = nCTDIw * mAs_rot * k_OB / pitch * (kVp/kV_ref)^2"
        notes.append(
            f"tube-voltage corrected from {float(coef.kv_reference):g} kVp "
            f"to {float(acq.kvp):g} kVp by (kVp/kV_ref)^2 = {k_kv:.4f}"
        )
    elif kv_correction and acq.kvp is not None and coef.kv_reference:
        notes.append(
            f"tube voltage {float(acq.kvp):g} kVp equals reference "
            f"{float(coef.kv_reference):g} kVp; no correction needed"
        )

    return Quantity(
        value=value,
        unit="mGy",
        source=Source.PHYSICS_FALLBACK,
        formula=formula,
        inputs=inputs,
        reference="legacy scanner coefficient table",
        notes=tuple(notes),
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


def ctdivol(
    acq: Acquisition,
    coef: ScannerCoefficients,
    *,
    recorded_mgy: float | Quantity | None = None,
    recorded_source: Source = Source.DICOM_TAG,
    prefer_recorded: bool = True,
    kv_correction: bool = True,
) -> Quantity:
    """CTDIvol, preferring the recorded value and falling back to physics.

    Args:
        recorded_mgy: a recorded CTDIvol, either as a bare number or as a
            :class:`~ctdose_core.provenance.Quantity` that already carries its origin.
        recorded_source: origin to record when ``recorded_mgy`` is a bare number.
        kv_correction: forwarded to :func:`estimate_ctdivol` for the physics
            fallback (tube-voltage scaling of the tabulated nCTDIw).
    """
    if prefer_recorded and recorded_mgy:
        if isinstance(recorded_mgy, Quantity):
            return recorded_mgy
        source_labels = {
            Source.RDSR: ("CT Radiation Dose SR, Mean CTDIvol", "DCM 113830"),
            Source.DICOM_TAG: ("CTDI Volume (0018,9345)", "(0018,9345)"),
        }
        formula, tag = source_labels.get(recorded_source, ("recorded CTDIvol", ""))
        return Quantity(
            value=float(recorded_mgy),
            unit="mGy",
            source=recorded_source,
            formula=formula,
            inputs={"tag": tag} if tag else {},
        )
    if coef.nctdi_w is None:
        raise ValueError("no nCTDIw coefficient for this scanner/region/size combination")
    return estimate_ctdivol(acq, coef, kv_correction=kv_correction)


def irradiated_length_cm(acq: Acquisition, coef: ScannerCoefficients) -> Quantity:
    """Irradiated length ``(scan range + over-ranging)`` in cm."""
    _, l_rot = _geometry(acq, coef)
    value = (acq.scan_range_mm + float(l_rot)) / 10.0
    return Quantity(
        value=value,
        unit="cm",
        source=Source.DERIVED,
        formula="L = (scan_range + L_rot) / 10",
        inputs={"scan_range_mm": acq.scan_range_mm, "L_rot_mm": float(l_rot)},
    )


def estimate_dlp(acq: Acquisition, coef: ScannerCoefficients, ctdivol_mgy: Quantity | float) -> Quantity:
    """DLP from CTDIvol and the irradiated length, ``DLP = CTDIvol * L``."""
    length = irradiated_length_cm(acq, coef)
    c = float(ctdivol_mgy)
    value = c * float(length)
    inherits = (
        ctdivol_mgy.source if isinstance(ctdivol_mgy, Quantity) else Source.USER
    )
    return Quantity(
        value=value,
        unit="mGy*cm",
        source=Source.DERIVED,
        formula="DLP = CTDIvol * (scan_range + L_rot)/10",
        inputs={
            "CTDIvol_mGy": c,
            "CTDIvol_source": inherits.value,
            "L_cm": float(length),
            **length.inputs,
        },
        notes=("includes helical over-ranging",),
    )


def dlp(
    acq: Acquisition,
    coef: ScannerCoefficients,
    ctdivol_mgy: Quantity | float,
    *,
    recorded_mgycm: float | Quantity | None = None,
    recorded_source: Source = Source.DICOM_TEXT,
    prefer_recorded: bool = True,
) -> Quantity:
    """DLP, preferring a recorded value and falling back to ``CTDIvol x length``."""
    if prefer_recorded and recorded_mgycm:
        if isinstance(recorded_mgycm, Quantity):
            return recorded_mgycm
        source_labels = {
            Source.RDSR: ("CT Radiation Dose SR, DLP", "DCM 113838"),
            Source.DICOM_TEXT: ("recorded dose-length product", "(0040,0310)"),
        }
        formula, tag = source_labels.get(recorded_source, ("recorded DLP", ""))
        return Quantity(
            value=float(recorded_mgycm),
            unit="mGy*cm",
            source=recorded_source,
            formula=formula,
            inputs={"tag": tag} if tag else {},
        )
    return estimate_dlp(acq, coef, ctdivol_mgy)


def dlp_in_air(acq: Acquisition, coef: ScannerCoefficients) -> Quantity:
    """Free-in-air dose-length product, the input to the organ-conversion dose route.

    ``DLP_air = nCTDIair * mAs_rot * k_OB * (scan_range + L_rot)/10``
    """
    k_ob, _ = _geometry(acq, coef)
    length = irradiated_length_cm(acq, coef)
    value = coef.nctdi_air * acq.mas_per_rotation * float(k_ob) * float(length)
    return Quantity(
        value=value,
        unit="mGy*cm",
        source=Source.PHYSICS_FALLBACK,
        formula="DLP_air = nCTDIair * mAs_rot * k_OB * L",
        inputs={
            "nCTDIair_mGy_per_mAs": coef.nctdi_air,
            "mAs_rot": acq.mas_per_rotation,
            "k_OB": float(k_ob),
            "L_cm": float(length),
        },
        reference="CT-Expo free-in-air CTDI normalisation",
    )


def dose_metrics(
    acq: Acquisition,
    coef: ScannerCoefficients,
    *,
    recorded_ctdivol_mgy: float | Quantity | None = None,
    recorded_dlp_mgycm: float | Quantity | None = None,
    prefer_recorded: bool = True,
    kv_correction: bool = True,
) -> dict[str, Quantity]:
    """CTDIvol, DLP and DLP-in-air in one call."""
    c = ctdivol(
        acq,
        coef,
        recorded_mgy=recorded_ctdivol_mgy,
        prefer_recorded=prefer_recorded,
        kv_correction=kv_correction,
    )
    d = dlp(acq, coef, c, recorded_mgycm=recorded_dlp_mgycm, prefer_recorded=prefer_recorded)
    out: dict[str, Quantity] = {"ctdivol": c, "dlp": d}
    if coef.nctdi_air is not None:
        out["dlp_air"] = dlp_in_air(acq, coef)
    return out


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
