from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE_IMAGES = REPO / "data" / "DOSE_TARO"
ACQ_PARAMS = Path(__file__).parent / "data" / "dose_taro_acquisition.json"


@pytest.fixture(scope="session")
def dose_taro_params() -> dict:
    """Acquisition parameters of the full 147-slice DOSE TARO series."""
    if not ACQ_PARAMS.exists():
        pytest.skip(f"{ACQ_PARAMS} missing; run tools/make_fixture.py")
    return json.loads(ACQ_PARAMS.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def dose_taro_images() -> Path:
    """Directory of the bundled DICOM image fixture."""
    if not FIXTURE_IMAGES.exists() or not any(FIXTURE_IMAGES.glob("*.dcm")):
        pytest.skip(f"{FIXTURE_IMAGES} missing; run tools/make_fixture.py")
    return FIXTURE_IMAGES


@pytest.fixture(scope="session")
def dose_taro_acquisition(dose_taro_params):
    from ctdose_core.metrics import Acquisition

    p = dose_taro_params
    ma = float(np.mean(p["tube_current_ma"]))
    return Acquisition(
        mas_per_rotation=ma * p["rotation_time_s"],
        collimation_mm=p["collimation_mm"],
        pitch=p["pitch"],
        slice_thickness_mm=p["slice_thickness_mm"],
        scan_range_mm=p["scan_range_mm"],
        kvp=p["kvp"],
    )


def water_cylinder(
    radius_cm: float,
    hu: float = 0.0,
    *,
    matrix: int = 512,
    fov_cm: float = 50.0,
    background_hu: float = -1000.0,
) -> tuple[np.ndarray, tuple[float, float]]:
    """A synthetic axial slice holding a uniform disc of known radius and attenuation.

    Returns ``(slice_hu, pixel_spacing_mm)``. Used for the closed-form checks: a disc
    of radius r at 0 HU must give a water-equivalent diameter of exactly 2r.
    """
    spacing_mm = fov_cm * 10.0 / matrix
    centre = (matrix - 1) / 2.0
    yy, xx = np.mgrid[0:matrix, 0:matrix]
    r_pix = radius_cm * 10.0 / spacing_mm
    disc = ((yy - centre) ** 2 + (xx - centre) ** 2) <= r_pix**2
    img = np.full((matrix, matrix), background_hu, dtype=np.float64)
    img[disc] = hu
    return img, (spacing_mm, spacing_mm)
