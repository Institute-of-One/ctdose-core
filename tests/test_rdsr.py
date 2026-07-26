"""Vendor-neutral CT RDSR parsing.

These tests run against the OpenREM sample RDSR objects (GPL-3.0), which cover GE,
Siemens, Canon/Toshiba and Philips. The files are NOT vendored into this
(MIT-licensed) repository; point ``CTDOSE_RDSR_SAMPLES`` at a directory holding
them (e.g. obtained via ``pip download openrem --no-deps --no-binary :all:`` and
extracting ``openrem/remapp/tests/test_files/CT-*.dcm``). The tests skip cleanly
when the directory is absent so CI needs no third-party data.
"""

from __future__ import annotations

import contextlib
import glob
import os
from pathlib import Path

import pytest

from ctdose_core.provenance import Source
from ctdose_core.rdsr import read_rdsr

SAMPLES = os.environ.get("CTDOSE_RDSR_SAMPLES", "")
pytestmark = [
    pytest.mark.rdsr_samples,
    pytest.mark.skipif(
        not (SAMPLES and Path(SAMPLES).is_dir()),
        reason="set CTDOSE_RDSR_SAMPLES to a directory of CT RDSR objects to run",
    ),
]


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


# --- independent oracle -----------------------------------------------------------------
#
# The parser navigates the SR by template: it descends CT Acquisition -> CT Dose and
# reads the coded items it expects to find there. The oracle below does the opposite --
# it ignores the template entirely and sweeps the whole content tree for any NUM item
# carrying the code in question. If the parser's template navigation ever drops a
# branch, or picks up a value from the wrong container, the two disagree.


def _sweep(node, code: str) -> list[float]:
    """Every numeric value under ``node`` whose concept name is ``code``, template-blind."""
    found: list[float] = []
    for child in getattr(node, "ContentSequence", []) or []:
        seq = getattr(child, "ConceptNameCodeSequence", None)
        if seq and str(getattr(seq[0], "CodeValue", "")) == code:
            mvs = getattr(child, "MeasuredValueSequence", None)
            if mvs:
                with contextlib.suppress(TypeError, ValueError, AttributeError):
                    found.append(float(mvs[0].NumericValue))
        found.extend(_sweep(child, code))
    return found


def _sweep_codes(node, code: str) -> list[str]:
    """Every coded value under ``node`` whose concept name is ``code``."""
    found: list[str] = []
    for child in getattr(node, "ContentSequence", []) or []:
        seq = getattr(child, "ConceptNameCodeSequence", None)
        if seq and str(getattr(seq[0], "CodeValue", "")) == code:
            ccs = getattr(child, "ConceptCodeSequence", None)
            if ccs:
                found.append(str(getattr(ccs[0], "CodeMeaning", "")))
        found.extend(_sweep_codes(child, code))
    return found


@pytest.mark.parametrize("code,attr", [("113830", "ctdivol"), ("113838", "dlp")])
def test_parser_agrees_with_a_template_blind_pydicom_sweep(code, attr) -> None:
    """Every CTDIvol/DLP in the tree is found by the parser, and none is invented."""
    import pydicom

    checked = 0
    for f in _files():
        report = read_rdsr(f)
        if report is None:
            continue
        ds = pydicom.dcmread(f, force=True)
        oracle = sorted(_sweep(ds, code))
        parsed = sorted(
            float(getattr(a, attr)) for a in report.acquisitions if getattr(a, attr) is not None
        )
        assert parsed == pytest.approx(oracle), f"{os.path.basename(f)}: {attr}"
        checked += 1
    assert checked >= 14


def test_phantom_codes_agree_with_the_sweep() -> None:
    """Every CTDIw phantom code in the tree is accounted for.

    A phantom code appears once per irradiation event, and some vendors attach one to
    the accumulated DLP as well; both are parsed, so the two sets must match exactly.
    """
    import pydicom

    for f in _files():
        report = read_rdsr(f)
        if report is None:
            continue
        ds = pydicom.dcmread(f, force=True)
        oracle = sorted(_sweep_codes(ds, "113835"))
        parsed = [a.phantom_type for a in report.acquisitions if a.phantom_type]
        if report.total_dlp_phantom_type:
            parsed.append(report.total_dlp_phantom_type)
        assert sorted(parsed) == oracle, os.path.basename(f)


def test_accumulated_phantom_agrees_with_the_events_that_produced_it() -> None:
    """Where an accumulated-level phantom is stated, the events must not contradict it."""
    seen = 0
    for f in _files():
        report = read_rdsr(f)
        if report is None or not report.total_dlp_phantom_type:
            continue
        seen += 1
        event_phantoms = {a.phantom_type for a in report.acquisitions if a.phantom_type}
        assert report.total_dlp_phantom_type in event_phantoms, os.path.basename(f)
    assert seen >= 1


def test_accumulated_dlp_agrees_with_the_sweep() -> None:
    import pydicom

    for f in _files():
        report = read_rdsr(f)
        if report is None:
            continue
        ds = pydicom.dcmread(f, force=True)
        oracle = _sweep(ds, "113813")
        if not oracle:
            assert report.total_dlp is None, os.path.basename(f)
            continue
        assert report.total_dlp is not None, os.path.basename(f)
        assert float(report.total_dlp) == pytest.approx(oracle[0])


def test_summed_event_dlp_matches_the_recorded_accumulated_total() -> None:
    """Internal consistency: Sigma(event DLP) == the SR's own accumulated DLP.

    Two independently recorded quantities inside the same object. Agreement across all
    four vendors is the strongest evidence available that the per-event walk is complete.
    """
    compared = 0
    for f in _files():
        report = read_rdsr(f)
        if report is None or report.total_dlp is None:
            continue
        summed = report.summed_event_dlp
        if summed is None:
            continue
        total = float(report.total_dlp)
        assert summed == pytest.approx(total, rel=0.01), os.path.basename(f)
        compared += 1
    assert compared >= 14


def test_declared_event_count_matches_the_number_parsed() -> None:
    """The SR states how many irradiation events it holds; parse exactly that many."""
    from tools.make_rdsr_layer1 import declared_event_count

    compared = 0
    for f in _files():
        report = read_rdsr(f)
        if report is None:
            continue
        declared = declared_event_count(Path(f))
        if declared is None:
            continue
        assert len(report.acquisitions) == declared, os.path.basename(f)
        compared += 1
    assert compared >= 14
