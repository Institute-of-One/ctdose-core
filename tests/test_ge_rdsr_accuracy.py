"""The GE validation arm: its eligibility rules, its arithmetic, and its refusals.

Most of this needs no third-party data, because what has to be right is the rule set
rather than the particular objects. The rules decide which events enter the comparison,
and a rule that silently admits a cine acquisition or silently aliases one scanner to
another would put a wrong number in the manuscript with every gate still green. Each
test below injects the defect it is meant to catch.

The two tests that do read the sample objects skip cleanly when
``CTDOSE_RDSR_SAMPLES`` is unset, as in ``test_rdsr.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ctdose_core.ctdi_table import CoefficientNotAvailable, resolve_model
from ctdose_core.provenance import Quantity, Source
from ctdose_core.rdsr import RdsrAcquisition
from tools.evaluate_ge_rdsr import (
    HELICAL_TYPES,
    _slug,
    beam_width_consistency,
    check_formula_inputs,
    evaluate_event,
    exclusion_reason,
    run,
    technique_complete,
)

SAMPLES = os.environ.get("CTDOSE_RDSR_SAMPLES", "")
needs_samples = pytest.mark.skipif(
    not (SAMPLES and Path(SAMPLES).is_dir()),
    reason="set CTDOSE_RDSR_SAMPLES to a directory of CT RDSR objects to run",
)


def _event(**overrides) -> RdsrAcquisition:
    """A complete helical GE event, so a test can spoil exactly one thing."""
    defaults = dict(
        acquisition_type="Spiral Acquisition",
        ctdivol=Quantity(value=8.74, unit="mGy", source=Source.RDSR),
        phantom_type="IEC Body Dosimetry Phantom",
        kvp=120.0,
        mean_tube_current_ma=200.0,
        exposure_time_per_rotation_s=0.5,
        pitch=0.97,
        total_collimation_mm=2.5,
        scanning_length_mm=468.1,
    )
    defaults.update(overrides)
    return RdsrAcquisition(**defaults)


# --- the eligibility rules -------------------------------------------------------------


def test_only_acquisitions_with_table_feed_are_helical() -> None:
    """The acquisition types that irradiate without table feed must not be admitted.

    This is the rule that matters most. GE's reports carry stationary and cine events
    whose recorded CTDIvol accumulates over the dwell: event 22 of the VCT report records
    176 mGy from 80 mAs per rotation, which the single-rotation formula would under-
    predict by a factor of twenty and call model error.
    """
    for kind in (
        "Stationary Acquisition",
        "Sequenced Acquisition",
        "Constant Angle Acquisition",
        "Free Acquisition",
    ):
        assert _slug(kind) not in HELICAL_TYPES, f"{kind} has no table feed"
    assert _slug("Spiral Acquisition") in HELICAL_TYPES


def test_incomplete_technique_is_named_field_by_field() -> None:
    """Each missing technique field is reported, so an exclusion says what was absent."""
    assert technique_complete(_event()) == []
    assert technique_complete(_event(kvp=None)) == ["tube potential"]
    assert technique_complete(_event(pitch=None)) == ["pitch"]
    assert technique_complete(_event(phantom_type="")) == ["CTDIw phantom code"]
    # A zero is as unusable as an absence and must not pass as a value.
    assert technique_complete(_event(exposure_time_per_rotation_s=0.0)) == [
        "exposure time per rotation"
    ]
    several = technique_complete(_event(kvp=None, mean_tube_current_ma=None))
    assert several == ["tube potential", "mean tube current"]


def test_a_complete_helical_event_is_admitted_and_each_rule_fires_in_order() -> None:
    """The rule set is one function, and an event is counted against the first rule it
    fails.

    Order matters for the exclusion tally in the paper. An event with neither a recorded
    CTDIvol nor a resolvable scanner must be counted once, under the missing reference,
    not twice and not under whichever check happened to run.
    """
    resolved = ("GE", "LightSpeed VCT")
    assert exclusion_reason(_event(), resolved=resolved) == ("", "")

    no_reference = exclusion_reason(_event(ctdivol=None), resolved=None)
    assert no_reference[0] == "no recorded CTDIvol to compare against"

    unresolved = exclusion_reason(_event(), resolved=None, refusal="not in the database")
    assert unresolved[0] == "scanner not in the open coefficient database"
    assert unresolved[1] == "not in the database"

    cine = exclusion_reason(
        _event(acquisition_type="Stationary Acquisition"), resolved=resolved
    )
    assert "accumulates over the dwell" in cine[0]

    unknown_type = exclusion_reason(
        _event(acquisition_type="Some Future Acquisition"), resolved=resolved
    )
    assert unknown_type[0] == "acquisition type is not a helical scan"

    incomplete = exclusion_reason(_event(pitch=None), resolved=resolved)
    assert incomplete == ("incomplete technique", "missing: pitch")


def test_lightspeed_rt16_does_not_resolve_to_lightspeed_16() -> None:
    """The RT16 is a different machine and must not inherit the LightSpeed 16's output.

    ``LightSpeed RT16`` is the radiotherapy configuration, with an 80 cm bore against the
    diagnostic LightSpeed 16's 70 cm. Bore size changes the geometry the coefficient was
    measured in, so the names denoting *almost* the same scanner is exactly the case
    ``resolve_model`` refuses by design. Two of the fifteen GE events with a recorded
    CTDIvol are lost to this, and that is the correct outcome.
    """
    with pytest.raises(CoefficientNotAvailable):
        resolve_model("GE MEDICAL SYSTEMS", "LightSpeed RT16")

    # The diagnostic model and the two that do resolve, for contrast.
    assert resolve_model("GE MEDICAL SYSTEMS", "LightSpeed 16") == ("GE", "LightSpeed 16")
    assert resolve_model("GE Medical Systems", "LightSpeed VCT") == ("GE", "LightSpeed VCT")
    assert resolve_model("GE Medical Systems", "Optima CT660") == ("GE", "Optima 660")


# --- the arithmetic --------------------------------------------------------------------


def test_the_error_is_measured_against_the_recorded_value() -> None:
    """The reference belongs in the denominator.

    ``|estimate/reference - 1|`` and ``|reference/estimate - 1|`` are different
    functions, and the scanner's own CTDIvol is the reference here. The expected figure
    is computed in the test from the division written out, so a rescoring that kept the
    wording could not pass.
    """
    case = evaluate_event(
        file_name="synthetic.dcm",
        vendor="GE",
        model="LightSpeed VCT",
        manufacturer_raw="GE Medical Systems",
        model_raw="LightSpeed VCT",
        index=0,
        acq=_event(),
    )
    recorded = case["recorded_ctdivol_mgy"]
    estimated = case["estimated_ctdivol_mgy"]
    assert recorded == 8.74
    assert case["percent_error"] == pytest.approx(
        100.0 * (estimated - recorded) / recorded
    )
    # And it is NOT the inverted measure, which for this case differs by over a point.
    inverted = 100.0 * (recorded - estimated) / estimated
    assert abs(case["percent_error"] - inverted) > 1.0

    # The prediction itself: CTDIw * (mAs_rot / 100) / pitch, mAs_rot = 200 mA * 0.5 s.
    assert case["mas_per_rotation"] == pytest.approx(100.0)
    assert case["estimated_ctdivol_mgy"] == pytest.approx(
        case["table_ctdiw_mgy_per_100mas"] * (100.0 / 100.0) / 0.97
    )


def test_the_implied_coefficient_inverts_the_formula() -> None:
    """The coefficient implied by the recorded value must invert the prediction exactly.

    This is what shows the formula's structure is right independently of which table row
    is picked: across both GE models the implied coefficient lands in a narrow band.
    """
    case = evaluate_event(
        file_name="synthetic.dcm",
        vendor="GE",
        model="LightSpeed VCT",
        manufacturer_raw="GE Medical Systems",
        model_raw="LightSpeed VCT",
        index=0,
        acq=_event(),
    )
    implied = case["implied_nctdi_w_mgy_per_100mas"]
    assert implied == pytest.approx(8.74 * 0.97 / 1.0)
    # Feeding the implied coefficient back through the formula returns the recorded value.
    assert implied * (case["mas_per_rotation"] / 100.0) / case["pitch"] == pytest.approx(
        case["recorded_ctdivol_mgy"]
    )


def test_beam_width_check_computes_the_scan_duration() -> None:
    """Rotations and duration, against values computed by hand.

    468.1 mm at a 2.5 mm beam and pitch 0.97 is 468.1 / 2.425 = 193.0 rotations, and at
    0.5 s each that is 96.5 s of continuous rotation for one body pass -- the figure that
    shows the recorded field is not a total beam width. The same pass at 40 mm is 6.0 s.
    """
    out = beam_width_consistency(_event())
    at = out["at_collimation_mm"]
    assert at["2.5"]["rotations"] == pytest.approx(468.1 / (2.5 * 0.97))
    assert at["2.5"]["scan_duration_s"] == pytest.approx(96.51, abs=0.05)
    assert at["2.5"]["is_the_recorded_value"] is True
    assert at["40"]["scan_duration_s"] == pytest.approx(6.03, abs=0.05)
    assert at["40"]["is_the_recorded_value"] is False
    # The implausible duration is the evidence, so it must be an order of magnitude out.
    assert at["2.5"]["scan_duration_s"] / at["40"]["scan_duration_s"] > 10

    # An event with no recorded length cannot support the check and must say so.
    assert beam_width_consistency(_event(scanning_length_mm=0.0))["at_collimation_mm"] == {}


def test_the_prediction_ignores_the_fields_an_rdsr_does_not_record() -> None:
    """Slice thickness and scan range are supplied by the script and must not be used.

    ``Acquisition`` validates both, an RDSR records neither, so the script passes the
    collimation and the scanning length. If the formula ever started reading them, every
    number in this arm would acquire an invented input.
    """
    check = check_formula_inputs()
    assert check["invariant"] is True
    assert (
        check["estimate_with_5mm_slice_100mm_range"]
        == check["estimate_with_0.625mm_slice_700mm_range"]
    )


# --- against the real objects ----------------------------------------------------------


@needs_samples
def test_the_run_admits_only_helical_ge_events_with_a_reference() -> None:
    """End to end: every admitted case satisfies every stated rule."""
    payload = run(Path(SAMPLES))
    summary = payload["summary"]

    assert summary["n_files"] == 3, "three GE dose reports are expected in the samples"
    assert summary["n_events_with_ctdivol"] == 15
    assert summary["n_evaluated"] > 0
    assert summary["n_evaluated"] + summary["n_excluded"] == summary["n_events_seen"]

    for case in payload["cases"]:
        assert _slug(case["acquisition_type"]) in HELICAL_TYPES
        assert case["table_vendor"] == "GE"
        assert case["recorded_ctdivol_mgy"] > 0
        assert case["phantom_cm"] in (16, 32)
        assert case["pitch"] > 0

    # The RT16 events are excluded for the model, not quietly admitted or dropped as
    # incomplete; its two events are the only ones refused on the scanner.
    refused = [
        e for e in payload["excluded"]
        if e["reason"] == "scanner not in the open coefficient database"
    ]
    assert len(refused) == 2
    assert {e["model_raw"] for e in refused} == {"LightSpeed RT16"}


@needs_samples
def test_injecting_a_cine_event_into_the_comparison_moves_the_result() -> None:
    """Prove the helical rule is load-bearing rather than decorative.

    Event 22 of the VCT report is a stationary acquisition recording 176.12 mGy from
    200 mA and 0.4 s. Predicting it with the single-rotation formula is what the rule
    prevents; this test performs that prediction directly and requires the error to be
    large, so a future change that admitted such events could not pass unnoticed.
    """
    case = evaluate_event(
        file_name="injected.dcm",
        vendor="GE",
        model="LightSpeed VCT",
        manufacturer_raw="GE Medical Systems",
        model_raw="LightSpeed VCT",
        index=22,
        acq=_event(
            acquisition_type="Stationary Acquisition",
            ctdivol=Quantity(value=176.12, unit="mGy", source=Source.RDSR),
            mean_tube_current_ma=200.0,
            exposure_time_per_rotation_s=0.4,
            pitch=1.0,
            total_collimation_mm=20.0,
            scanning_length_mm=18.8,
        ),
    )
    assert case["percent_error"] < -80.0, (
        "a cine event's accumulated CTDIvol is far above one rotation's worth; if this "
        "no longer under-predicts, the exclusion rule has stopped being necessary and "
        "the reason in the provenance block needs rewriting"
    )

    # And the real run must not contain it.
    payload = run(Path(SAMPLES))
    assert not [
        c for c in payload["cases"]
        if c["file"] == "CT-ESR-GE_VCT.dcm" and c["event_index"] == 22
    ]
