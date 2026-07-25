"""Body-contour extraction with no AI dependency.

The reference implementation used fuzzy c-means thresholding followed by
``bwareaopen`` / ``imfill``. The equivalent here is a fixed attenuation threshold,
largest-connected-component selection and hole filling, which is deterministic,
dependency-light and reproducible across platforms. It is the fallback path when
TotalSegmentator is not installed, and the sanity check when it is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

#: Air/tissue boundary. Everything above this is treated as patient or table.
DEFAULT_THRESHOLD_HU = -300.0

#: Connected components smaller than this (in cm^2) are discarded as noise or table.
MIN_COMPONENT_AREA_CM2 = 20.0


@dataclass(frozen=True)
class BodyContour:
    """A single-slice body contour and the size metrics derived from it."""

    mask: np.ndarray
    area_cm2: float
    mean_hu: float
    equivalent_diameter_cm: float
    ap_cm: float
    lateral_cm: float

    @property
    def is_empty(self) -> bool:
        return self.area_cm2 <= 0


def body_mask(
    slice_hu: np.ndarray,
    pixel_spacing_mm: tuple[float, float],
    *,
    threshold_hu: float = DEFAULT_THRESHOLD_HU,
    min_area_cm2: float = MIN_COMPONENT_AREA_CM2,
) -> np.ndarray:
    """Binary patient mask for one axial slice.

    Thresholds at ``threshold_hu``, keeps the largest connected component above
    ``min_area_cm2`` (which drops the table and any support pads that are not touching
    the patient), then fills interior holes so that lung and bowel gas count as
    patient volume -- as they must for a water-equivalent diameter.
    """
    if slice_hu.ndim != 2:
        raise ValueError("slice_hu must be a 2-D array")
    py, px = pixel_spacing_mm
    pixel_area_cm2 = (py / 10.0) * (px / 10.0)

    binary = slice_hu > threshold_hu
    if not binary.any():
        return np.zeros_like(binary, dtype=bool)

    # Erode-then-restore so a thin contact with the table does not merge the two.
    opened = ndimage.binary_opening(binary, structure=np.ones((3, 3)), iterations=2)
    labels, n = ndimage.label(opened)
    if n == 0:
        return np.zeros_like(binary, dtype=bool)
    sizes = ndimage.sum(opened, labels, index=range(1, n + 1))
    order = np.argsort(sizes)[::-1]
    keep = None
    for idx in order:
        if sizes[idx] * pixel_area_cm2 >= min_area_cm2:
            keep = idx + 1
            break
    if keep is None:
        keep = int(order[0]) + 1
    mask = labels == keep
    mask = ndimage.binary_closing(mask, structure=np.ones((5, 5)))
    return ndimage.binary_fill_holes(mask)


def contour_metrics(
    slice_hu: np.ndarray,
    pixel_spacing_mm: tuple[float, float],
    *,
    mask: np.ndarray | None = None,
    **kwargs,
) -> BodyContour:
    """Area, mean HU and outline dimensions of the body contour of one slice."""
    if mask is None:
        mask = body_mask(slice_hu, pixel_spacing_mm, **kwargs)
    py, px = pixel_spacing_mm
    pixel_area_cm2 = (py / 10.0) * (px / 10.0)
    n = int(mask.sum())
    if n == 0:
        return BodyContour(mask, 0.0, 0.0, 0.0, 0.0, 0.0)
    area = n * pixel_area_cm2
    mean_hu = float(slice_hu[mask].mean())
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    ap = float((rows[-1] - rows[0] + 1) * py / 10.0)
    lateral = float((cols[-1] - cols[0] + 1) * px / 10.0)
    equiv = float(2.0 * np.sqrt(area / np.pi))
    return BodyContour(
        mask=mask,
        area_cm2=area,
        mean_hu=mean_hu,
        equivalent_diameter_cm=equiv,
        ap_cm=ap,
        lateral_cm=lateral,
    )


def contour_profile(
    volume_hu: np.ndarray,
    pixel_spacing_mm: tuple[float, float],
    *,
    step: int = 1,
    **kwargs,
) -> list[BodyContour]:
    """Body contour for every ``step``-th slice of a volume."""
    return [
        contour_metrics(volume_hu[i], pixel_spacing_mm, **kwargs)
        for i in range(0, volume_hu.shape[0], step)
    ]


def central_slice_index(volume_hu: np.ndarray) -> int:
    """Index of the mid-scan slice -- the reference implementation's default ROI."""
    return volume_hu.shape[0] // 2
