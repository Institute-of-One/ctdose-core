"""Vendor-neutral CT RDSR parsing.

These tests run against the OpenREM sample RDSR objects (GPL-3.0), which cover GE,
Siemens, Canon/Toshiba and Philips. The files are NOT vendored into this
(MIT-licensed) repository; point ``CTDOSE_RDSR_SAMPLES`` at a directory holding
them (e.g. obtained via ``pip download openrem --no-deps --no-binary :all:`` and
extracting ``openrem/remapp/tests/test_files/CT-*.dcm``). The tests skip cleanly
when the directory is absent so CI needs no third-party data.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from ctdose_core.provenance import Source
from ctdose_core.rdsr import read_rdsr

SAMPLES = os.environ.get("CTDOSE_RDSR_SAMPLES", "")
pytestmark = pytest.mark.skipif(
    not (SAMPLES and Path(SAMPLES).is_dir()),
    reason="set CTDOSE_RDSR_SAMPLES to a directory of CT RDSR objects to run",
)


def _files() -> list[str]:
    return sorted(glob.glob(os.path.join(SAMPLES, "CT-*.dcm")))


def test_multivendor_ctdivol_and_dlp_parse() -> None:
    """Every CT RDSR yields at least one acquisition with an RDSR-sourced CTDIvol."""
    seen_vendors: set[str] = set()
    parsed = 0
    for f in _files():
        report = read_rdsr(f)
        if report is None:
            continue  # non-SR dose screens are correctly rejected
        parsed += 1
        assert report.acquisitions, f"{f}: no acquisitions parsed"
        got_ctdi = [a.ctdivol for a in report.acquisitions if a.ctdivol]
        assert got_ctdi, f"{f}: no CTDIvol found"
        for q in got_ctdi:
            assert q.source is Source.RDSR
            assert q.unit.lower().startswith("mgy")
            assert float(q) > 0
        seen_vendors.add(report.manufacturer.split()[0].upper())
    assert parsed >= 3
    # GE, SIEMENS and TOSHIBA(=Canon) must all be represented.
    assert {"GE", "SIEMENS", "TOSHIBA"} <= seen_vendors


def test_head_vs_body_phantom_distinguished() -> None:
    """The parser must read the CTDIw phantom type (16 cm head vs 32 cm body)."""
    phantoms = set()
    for f in _files():
        report = read_rdsr(f)
        if report is None:
            continue
        for a in report.acquisitions:
            if a.phantom_type:
                phantoms.add(a.phantom_type)
    assert any("Head" in p for p in phantoms)
    assert any("Body" in p for p in phantoms)


def test_non_rdsr_returns_none() -> None:
    """A secondary-capture dose screen (Modality CT) is not a CT RDSR."""
    sc = [f for f in glob.glob(os.path.join(SAMPLES, "*.dcm")) if "-SC-" in f]
    for f in sc:
        assert read_rdsr(f) is None
