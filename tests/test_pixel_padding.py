"""Pixel Padding Value decoding.

The value representation of Pixel Padding Value (0028,0120) depends on Pixel
Representation, and implementations do not reliably honour that. A vendor writing
``63536`` with VR US on signed data means ``-2000``; read literally it puts the padding
threshold above every Hounsfield value in the image, and the whole volume becomes air.

That failure has no downstream symptom worth the name: the body contour finds no
patient, and anything measured from the volume quietly becomes nothing. These tests fix
both the specific encoding and the general case -- a padding rule that would blank the
image is refused whatever produced it.
"""

from __future__ import annotations

import numpy as np
import pydicom
import pytest

from ctdose_core.io import _padding_value, load_series


def _dataset(*, padding=None, padding_vr="US", pixel_representation=1, z=0.0):
    meta = pydicom.dataset.FileMetaDataset()
    meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
    meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    ds = pydicom.dataset.FileDataset("s.dcm", {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = pydicom.uid.CTImageStorage
    ds.SOPInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID = "1.2.3.4"
    ds.StudyInstanceUID = "1.2.3"
    ds.Modality = "CT"
    ds.Rows = ds.Columns = 32
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = pixel_representation
    ds.RescaleSlope = 1
    ds.RescaleIntercept = -1024
    ds.SliceThickness = 5.0
    ds.PixelSpacing = [1.0, 1.0]
    ds.ImagePositionPatient = [0.0, 0.0, z]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.SliceLocation = z
    ds.XRayTubeCurrent = 200
    ds.KVP = 120.0

    raw = np.full((32, 32), -2000, dtype=np.int16)
    yy, xx = np.mgrid[0:32, 0:32]
    raw[(yy - 16) ** 2 + (xx - 16) ** 2 < 10**2] = 1074  # +50 HU
    ds.PixelData = raw.tobytes()

    if padding is not None:
        ds.add_new((0x0028, 0x0120), padding_vr, padding)
    return ds


def _series(tmp_path, **kwargs):
    for i in range(3):
        ds = _dataset(z=i * 5.0, **kwargs)
        ds.save_as(tmp_path / f"s{i}.dcm", enforce_file_format=True)
    return load_series(tmp_path)


# --- the attribute ---------------------------------------------------------------------


def test_an_unsigned_encoded_padding_value_on_signed_data_reads_as_negative():
    assert _padding_value(_dataset(padding=63536, padding_vr="US")) == -2000.0


def test_the_same_value_on_unsigned_data_is_left_alone():
    assert _padding_value(_dataset(padding=63536, padding_vr="US", pixel_representation=0)) == 63536.0


def test_a_conventionally_encoded_padding_value_is_unchanged():
    assert _padding_value(_dataset(padding=-2000, padding_vr="SS")) == -2000.0


def test_a_series_without_the_attribute_has_no_padding_value():
    assert _padding_value(_dataset()) is None


# --- the volume ------------------------------------------------------------------------


def test_the_patient_survives_an_unsigned_encoded_padding_value(tmp_path):
    """Before the fix this volume came back as uniform air and nothing reported it."""
    volume = _series(tmp_path, padding=63536, padding_vr="US").load_volume()
    assert volume.max() == pytest.approx(50.0)
    assert volume.min() == pytest.approx(-1000.0)


def test_padding_declared_the_correct_way_round_behaves_identically(tmp_path):
    volume = _series(tmp_path, padding=-2000, padding_vr="SS").load_volume()
    assert volume.max() == pytest.approx(50.0)
    assert volume.min() == pytest.approx(-1000.0)


def test_a_slice_with_no_padding_attribute_keeps_its_values(tmp_path):
    volume = _series(tmp_path).load_volume()
    assert volume.min() == pytest.approx(-3024.0)


def test_a_padding_rule_that_would_blank_the_image_is_refused_and_recorded(tmp_path):
    series = _series(tmp_path, padding=32000, padding_vr="SS")
    volume = series.load_volume()
    assert volume.max() == pytest.approx(50.0), "the image must survive a bad padding rule"
    assert any("would blank" in w for w in series.warnings)
