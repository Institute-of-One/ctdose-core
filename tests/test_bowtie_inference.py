"""Inferring the bowtie / scan-field qualifier from the DICOM scan field of view.

DICOM does not record the selected bowtie filter. For scanners whose coefficients
depend on the scan field of view, it is inferred from Data Collection Diameter
(0018,0090), falling back to Reconstruction Diameter (0018,1100), and surfaced as an
assumption. An explicit ``bowtie=`` always overrides the inference.
"""

from __future__ import annotations

import pytest

from ctdose_core.scanners import (
    bowtie_from_scan_fov,
    has_bowtie_variants,
)


@pytest.mark.parametrize(
    "diameter_mm,expected",
    [
        (250.0, "small"),
        (300.0, "small"),
        (380.0, "medium"),
        (450.0, "medium"),
        (500.0, "large"),
        (None, None),
        (0.0, None),
        (-1.0, None),
    ],
)
def test_bowtie_from_scan_fov(diameter_mm, expected):
    assert bowtie_from_scan_fov(diameter_mm) == expected


def test_has_bowtie_variants_true_for_ge_lightspeed_vct():
    # The GE LightSpeed VCT family has multiple bowtie/scan-field rows.
    assert has_bowtie_variants("LightSpeed VCT", "GE MEDICAL SYSTEMS") is True


def test_has_bowtie_variants_false_for_single_row_model():
    # The Siemens Definition AS family has a single coefficient row (bowtie is moot).
    assert has_bowtie_variants("SOMATOM Definition AS+", "SIEMENS") is False
