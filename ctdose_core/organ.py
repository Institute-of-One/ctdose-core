"""Organ-level dose weighting from automated anatomy (optional ``[ai]`` extra).

The core package never imports TotalSegmentator. This module does, lazily, and every
public function degrades to the contour-based path in :mod:`ctdose_core.segmentation`
when it is absent -- so ``ctdose-core`` remains installable and useful without a deep
learning stack.

What is computed here is a **relative organ dose profile**: the tube-current-weighted
share of the delivered dose that each organ's longitudinal extent receives. Given a
slice-wise tube current :math:`I(z)` from (0018,1151) and an organ mask occupying
slices :math:`Z_o` with per-slice voxel counts :math:`n_o(z)`,

.. math::

    w_o = \\frac{\\sum_{z \\in Z_o} n_o(z) I(z)}{\\sum_{z \\in Z_o} n_o(z)}
          \\Big/ \\frac{1}{|Z|}\\sum_{z \\in Z} I(z)

so :math:`w_o` is the organ's mA-weighted exposure relative to the scan mean; the
organ's relative dose index is :math:`\\mathrm{CTDI_{vol}} \\cdot w_o`.

This is an exposure-weighting model, not a Monte-Carlo organ dose. Converting
:math:`w_o` to absolute organ dose in mGy requires scanner- and phantom-specific
normalisation coefficients from a published Monte-Carlo or measurement study;
:func:`scale_to_absolute` applies such coefficients when the caller supplies them and
refuses to invent them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .io import ScanSeries
from .provenance import Quantity, Source
from .ssde import water_equivalent_diameter

#: A compact, dose-relevant organ subset. Segmenting only these (instead of the
#: full 100+ structures) makes TotalSegmentator dramatically faster and is all the
#: longitudinal dose-weighting / overlay needs.
DOSE_ROI_SUBSET = [
    "liver", "spleen", "kidney_left", "kidney_right", "pancreas", "stomach",
    "gallbladder", "adrenal_gland_left", "adrenal_gland_right", "aorta",
    "inferior_vena_cava", "urinary_bladder", "small_bowel", "colon",
    "spinal_cord", "lung_lower_lobe_left", "lung_lower_lobe_right",
]


class TotalSegmentatorUnavailable(ImportError):
    """Raised when the optional ``[ai]`` extra is required but not installed."""


def totalsegmentator_available() -> bool:
    """True when the optional TotalSegmentator dependency can be imported."""
    try:  # pragma: no cover - depends on optional install
        import totalsegmentator  # noqa: F401
    except ImportError:
        return False
    return True


def _require_totalsegmentator():  # pragma: no cover - depends on optional install
    try:
        from totalsegmentator.python_api import totalsegmentator
    except ImportError as exc:
        raise TotalSegmentatorUnavailable(
            "organ segmentation needs the optional extra: pip install 'ctdose-core[ai]'. "
            "TotalSegmentator is Apache-2.0 licensed and is used for inference only."
        ) from exc
    return totalsegmentator


@dataclass
class OrganWeight:
    """Tube-current-weighted exposure share for one organ."""

    organ: str
    n_voxels: int
    slice_span: tuple[int, int]
    mean_tube_current_ma: float
    relative_weight: float
    relative_dose_index_mgy: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "organ": self.organ,
            "n_voxels": self.n_voxels,
            "slice_span": list(self.slice_span),
            "mean_tube_current_ma": self.mean_tube_current_ma,
            "relative_weight": self.relative_weight,
            "relative_dose_index_mgy": self.relative_dose_index_mgy,
        }


@dataclass
class OrganDoseProfile:
    """Relative organ dose profile for one series."""

    weights: list[OrganWeight] = field(default_factory=list)
    scan_mean_tube_current_ma: float = 0.0
    ctdivol_mgy: float | None = None
    source: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scan_mean_tube_current_ma": self.scan_mean_tube_current_ma,
            "ctdivol_mgy": self.ctdivol_mgy,
            "organs": [w.to_dict() for w in self.weights],
            "notes": list(self.notes),
        }

    def as_mapping(self) -> dict[str, float]:
        return {w.organ: w.relative_weight for w in self.weights}


def tube_current_profile(series: ScanSeries) -> np.ndarray:
    """Per-slice tube current (0018,1151), ordered like the series' slices."""
    if series.tube_current_ma.size == 0:
        raise ValueError("series carries no per-slice tube current (0018,1151)")
    return np.asarray(series.tube_current_ma, dtype=float)


def organ_weights_from_masks(
    masks: Mapping[str, np.ndarray],
    tube_current_ma: Sequence[float],
    *,
    ctdivol_mgy: float | None = None,
    source: str = "user-supplied masks",
) -> OrganDoseProfile:
    """Tube-current-weighted exposure share for each supplied organ mask.

    Args:
        masks: organ name -> boolean array shaped ``(n_slices, rows, cols)``, or a
            per-slice voxel-count array shaped ``(n_slices,)``.
        tube_current_ma: per-slice tube current, same length as the slice axis.
        ctdivol_mgy: when given, each organ's relative dose index is reported as
            ``CTDIvol * w_o``.
    """
    ma = np.asarray(tube_current_ma, dtype=float)
    if ma.size == 0:
        raise ValueError("tube_current_ma is empty")
    scan_mean = float(ma.mean())
    if scan_mean <= 0:
        raise ValueError("mean tube current must be positive")

    weights: list[OrganWeight] = []
    for organ, mask in masks.items():
        arr = np.asarray(mask)
        counts = arr.astype(float) if arr.ndim == 1 else arr.reshape(arr.shape[0], -1).sum(axis=1)
        if counts.shape[0] != ma.shape[0]:
            raise ValueError(
                f"organ {organ!r}: mask has {counts.shape[0]} slices but "
                f"{ma.shape[0]} tube-current values were given"
            )
        total = counts.sum()
        if total <= 0:
            continue
        occupied = np.flatnonzero(counts > 0)
        organ_mean = float((counts * ma).sum() / total)
        weights.append(
            OrganWeight(
                organ=organ,
                n_voxels=int(total),
                slice_span=(int(occupied[0]), int(occupied[-1])),
                mean_tube_current_ma=organ_mean,
                relative_weight=organ_mean / scan_mean,
                relative_dose_index_mgy=(
                    None if ctdivol_mgy is None else float(ctdivol_mgy) * organ_mean / scan_mean
                ),
            )
        )
    weights.sort(key=lambda w: w.relative_weight, reverse=True)
    return OrganDoseProfile(
        weights=weights,
        scan_mean_tube_current_ma=scan_mean,
        ctdivol_mgy=ctdivol_mgy,
        source=source,
        notes=(
            "relative_weight is the organ's tube-current-weighted exposure divided by "
            "the scan mean; it is an exposure-weighting index, not an absolute organ "
            "dose. Use scale_to_absolute() with published normalisation coefficients "
            "to obtain mGy.",
        ),
    )


def load_masks_from_dir(mask_dir: Path | str, *, flip_lr: bool = False,
                        flip_ap: bool = False, flip_z: bool = False) -> dict[str, np.ndarray]:
    """Load per-organ boolean masks from a directory of TotalSegmentator ``.nii.gz`` files.

    This lets segmentation be run once in its own process (see ``examples/segment_fast.py``
    or the ``TotalSegmentator`` CLI) and the masks reused -- which is the reliable way on
    Windows, where running nnU-Net's multiprocessing *inside* Streamlit spawns runaway
    processes. Reading masks has no such problem.
    """
    files = sorted(Path(mask_dir).glob("*.nii.gz"))
    if not files:
        return {}
    import nibabel as nib

    masks: dict[str, np.ndarray] = {}
    for path in files:
        data = nib.load(str(path)).get_fdata()
        # TotalSegmentator writes (x, y, z); the dose model indexes by slice first.
        m = np.transpose(data > 0, (2, 1, 0))  # -> (z, y=AP, x=LR)
        if flip_z:
            m = m[::-1]
        if flip_ap:
            m = m[:, ::-1]
        if flip_lr:
            m = m[:, :, ::-1]
        masks[path.name.removesuffix(".nii.gz")] = m
    return masks


def segment_organs(
    series: ScanSeries,
    *,
    output_dir: Path | str | None = None,
    fast: bool = True,
    fastest: bool | None = None,
    task: str = "total",
    roi_subset: Sequence[str] | None = None,
    device: str | None = None,
    nr_thr_resamp: int | None = None,
    nr_thr_saving: int | None = None,
) -> dict[str, np.ndarray]:  # pragma: no cover - requires the optional extra + model weights
    """Run TotalSegmentator on a series and return per-organ boolean masks.

    Args:
        fast: use the low-resolution model. Adequate for longitudinal weighting, which
            only needs the organ's z-extent and rough voxel distribution.
        roi_subset: restrict inference to these structures.

    Raises:
        TotalSegmentatorUnavailable: if the ``[ai]`` extra is not installed.
    """
    import nibabel as nib  # noqa: F401  (pulled in by the extra)

    run = _require_totalsegmentator()
    input_dir = series.files[0].parent
    out = Path(output_dir) if output_dir else input_dir / "_ctdose_segmentation"
    out.mkdir(parents=True, exist_ok=True)
    call_kwargs = {
        "fast": fast,
        "task": task,
        "roi_subset": list(roi_subset) if roi_subset else None,
        "quiet": True,
    }
    # Version-robust extras: newer TotalSegmentator accepts these; drop them on older ones.
    extras = {
        "device": device,
        "fastest": fastest,
        "nr_thr_resamp": nr_thr_resamp,
        "nr_thr_saving": nr_thr_saving,
    }
    call_kwargs.update({k: v for k, v in extras.items() if v is not None})
    try:
        run(str(input_dir), str(out), **call_kwargs)
    except TypeError:
        run(str(input_dir), str(out), fast=fast, task=task,
            roi_subset=list(roi_subset) if roi_subset else None, quiet=True)
    return load_masks_from_dir(out)


def organ_dose_profile(
    series: ScanSeries,
    *,
    ctdivol_mgy: float | None = None,
    masks: Mapping[str, np.ndarray] | None = None,
    **segment_kwargs,
) -> OrganDoseProfile:
    """Relative organ dose profile, segmenting with TotalSegmentator if needed."""
    if masks is None:
        masks = segment_organs(series, **segment_kwargs)
        source = "TotalSegmentator (inference only, Apache-2.0)"
    else:
        source = "caller-supplied masks"
    return organ_weights_from_masks(
        masks, tube_current_profile(series), ctdivol_mgy=ctdivol_mgy, source=source
    )


def scale_to_absolute(
    profile: OrganDoseProfile,
    normalisation_mgy_per_mgy: Mapping[str, float],
    *,
    reference: str,
) -> dict[str, Quantity]:
    """Convert relative weights to absolute organ dose using published coefficients.

    Args:
        normalisation_mgy_per_mgy: organ -> organ dose per unit CTDIvol, from a
            published Monte-Carlo or measurement study.
        reference: citation for those coefficients. Required, and recorded in the
            provenance of every returned value -- absolute organ doses are only
            meaningful with their source attached.

    Raises:
        ValueError: if the profile has no CTDIvol, or ``reference`` is blank.
    """
    if profile.ctdivol_mgy is None:
        raise ValueError("profile has no CTDIvol; cannot scale to absolute dose")
    if not reference.strip():
        raise ValueError(
            "a citation for the normalisation coefficients is required; absolute organ "
            "doses must not be reported without their source"
        )
    out: dict[str, Quantity] = {}
    for w in profile.weights:
        coeff = normalisation_mgy_per_mgy.get(w.organ)
        if coeff is None:
            continue
        out[w.organ] = Quantity(
            value=profile.ctdivol_mgy * w.relative_weight * coeff,
            unit="mGy",
            source=Source.DERIVED,
            formula="D_organ = CTDIvol * w_organ * c_organ",
            inputs={
                "CTDIvol_mGy": profile.ctdivol_mgy,
                "w_organ": w.relative_weight,
                "c_organ_mGy_per_mGy": coeff,
                "segmentation_source": profile.source,
            },
            reference=reference,
        )
    return out


def water_equivalent_diameter_from_masks(
    volume_hu: np.ndarray,
    body_mask: np.ndarray,
    pixel_spacing_mm: tuple[float, float],
    *,
    slice_indices: Iterable[int] | None = None,
) -> Quantity:
    """Water-equivalent diameter using a supplied body mask (e.g. TotalSegmentator's).

    Provides the same measurement as the threshold-based path but with a
    deep-learning body contour, which is more robust when arms, table or immobilisation
    hardware are in the field of view.
    """
    py, px = pixel_spacing_mm
    pixel_area_cm2 = (py / 10.0) * (px / 10.0)
    indices = list(slice_indices) if slice_indices is not None else range(volume_hu.shape[0])
    values: list[float] = []
    for i in indices:
        mask = body_mask[i].astype(bool)
        n = int(mask.sum())
        if n == 0:
            continue
        values.append(
            float(water_equivalent_diameter(float(volume_hu[i][mask].mean()), n * pixel_area_cm2))
        )
    if not values:
        raise ValueError("body mask is empty on every requested slice")
    return Quantity(
        value=float(np.median(values)),
        unit="cm",
        source=Source.DERIVED,
        formula="median over slices of D_w = 2*sqrt((HU_mean/1000+1)*A_ROI/pi)",
        inputs={"n_slices": len(values), "mask_source": "supplied body mask"},
        reference="AAPM TG-220 (2014)",
    )
