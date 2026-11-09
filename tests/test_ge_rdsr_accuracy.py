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
    MOVING_TABLE_TYPES,
    STATIONARY_TYPES,
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
        exposure_time_total_s=12.67,
    )
    defaults.update(overrides)
    return RdsrAcquisition(**defaults)


# --- the eligibility rules -------------------------------------------------------------


def test_the_table_moves_in_a_sequenced_acquisition_as_well_as_a_spiral() -> None:
    """Two types qualify, not one, and the distinction is the table, not the label.

    This rule was wrong until 2026-11-08 and two independent reviewers caught it. A
    sequenced acquisition is axial step-and-shoot: in CID 10013 the table advances
    between rotations, so its CTDIvol is CTDIw x (N.T / increment), which is the same
    formula with pitch = increment / (N.T). It was being excluded for accumulating over
    a stationary dwell, which is what a *stationary* acquisition does. The cost was VCT
    event 12, which turned out to be the only event in the arm whose recorded geometry
    its own exposure time corroborates, and whose prediction is exact.
    """
    for kind in ("Spiral Acquisition", "Sequenced Acquisition"):
        assert _slug(kind) in MOVING_TABLE_TYPES, f"the table moves in a {kind}"
    for kind in (
        "Stationary Acquisition",
        "Constant Angle Acquisition",
        "Free Acquisition",
    ):
        assert _slug(kind) in STATIONARY_TYPES, f"the table does not move in a {kind}"
        assert _slug(kind) not in MOVING_TABLE_TYPES
    assert not set(MOVING_TABLE_TYPES) & set(STATIONARY_TYPES)


def test_a_sequenced_label_does_not_rescue_a_cine_series() -> None:
    """Type is necessary and not sufficient: the geometry has to agree with it.

    Four events in the sample are labelled sequenced and record a pitch of 0.100 over a
    scanning length of 0.0 mm. That is a cine series at one couch position wearing the
    label of a step-and-shoot. Admitting them because the label now qualifies would have
    been the opposite error to the one just fixed.
    """
    resolved = ("GE", "LightSpeed VCT")
    cine = _event(acquisition_type="Sequenced Acquisition", pitch=0.1,
                  scanning_length_mm=0.0)
    reason, _ = exclusion_reason(cine, resolved=resolved)
    assert "degenerate geometry" in reason
    assert "pitch" in reason

    # Zero travel at a pitch that *would* carry the table is contradictory rather than a
    # dwell, and gets its own reason. Until 2026-11-08 both cases returned the same
    # "degenerate geometry" reason, which made the pitch half of the rule above decide
    # nothing; a reviewer pointed that out.
    no_length = _event(
        acquisition_type="Sequenced Acquisition", pitch=0.97, scanning_length_mm=0.0
    )
    reason, _ = exclusion_reason(no_length, resolved=resolved)
    assert "contradictory geometry" in reason and "scanning length" in reason
    assert "degenerate geometry" not in reason

    # An absent scanning length is not evidence of zero travel at a *real* pitch: the
    # CTDIvol formula does not use the length, so the event is admitted and only the
    # width cross-check is lost.
    absent_length = _event(
        acquisition_type="Spiral Acquisition", pitch=0.97, scanning_length_mm=None
    )
    assert exclusion_reason(absent_length, resolved=resolved) == ("", ""), (
        "a missing DCM 113825 must not be read as a table that did not move"
    )

    # But a degenerate pitch with no recorded distance is still refused. Requiring the
    # zero to be *recorded*, which this rule did for one round after a reviewer objected
    # to firing on the pitch alone, let a pitch-0.100 cine series with no DCM 113825
    # through to be predicted by the single-rotation formula -- a tenfold error reported
    # as model error. Only a recorded positive distance shows the table moved.
    cine_no_length = _event(
        acquisition_type="Sequenced Acquisition", pitch=0.1, scanning_length_mm=None
    )
    reason, _ = exclusion_reason(cine_no_length, resolved=resolved)
    assert "degenerate geometry" in reason, reason
    assert "cannot carry the table" in reason

    # A recorded zero travel with no recorded pitch is an incomplete technique, and must
    # be counted rather than crash the run. Formatting the missing pitch into the
    # contradictory-geometry reason raised TypeError until 2026-11-08, and two reviewers
    # found the path on the same round.
    no_pitch = _event(
        acquisition_type="Sequenced Acquisition", pitch=None, scanning_length_mm=0.0
    )
    reason, detail = exclusion_reason(no_pitch, resolved=resolved)
    assert reason == "incomplete technique", reason
    assert "pitch" in detail

    real = _event(acquisition_type="Sequenced Acquisition", pitch=1.0,
                  scanning_length_mm=397.5)
    assert exclusion_reason(real, resolved=resolved) == ("", "")


def test_a_low_pitch_over_a_real_distance_is_a_feed_and_is_admitted() -> None:
    """A positive low pitch does not by itself establish that the table stood still.

    The rule fired on the pitch alone until 2026-11-08, which is wider than its own
    rationale: an event at pitch 0.15 over 300 mm has demonstrably moved 300 mm, and
    excluding it as a dwell would have been a silent loss. Two independent reviewers
    raised it. Both conditions are now required, and this test is the one that would
    have failed before the correction.
    """
    resolved = ("GE", "LightSpeed VCT")
    slow_feed = _event(
        acquisition_type="Spiral Acquisition", pitch=0.15, scanning_length_mm=300.0
    )
    assert exclusion_reason(slow_feed, resolved=resolved) == ("", ""), (
        "a low pitch over a real scanning length is a slow feed, not a dwell"
    )

    # And the pair is still refused, naming both halves of the reason.
    dwell = _event(
        acquisition_type="Sequenced Acquisition", pitch=0.15, scanning_length_mm=0.0
    )
    reason, _ = exclusion_reason(dwell, resolved=resolved)
    assert "degenerate geometry" in reason
    assert "pitch" in reason and "scanning length" in reason


def test_an_event_with_no_tabulated_cell_is_excluded_rather_than_crashing() -> None:
    """The envelope can be empty, and an empty envelope must not reach the headline.

    Every cell of the assumption grid is required to be an exactly tabulated row, so an
    event at an untabulated tube potential leaves none. Until 2026-11-08 that produced an
    envelope with no span, which _headline then read, and the whole run died with a
    KeyError -- the opposite of this script's claim that every exclusion is counted with
    a reason. A reviewer found the path. The event must now be refused through
    CoefficientNotAvailable, which the file loop already counts.
    """
    pytest.importorskip("pydicom")
    from ctdose_core.ctdi_table import CoefficientNotAvailable
    from tools.evaluate_ge_rdsr import evaluate_event  # noqa: PLC0415

    # 60 kVp is below every tabulated potential for this model, so the lookup refuses.
    acq = _event(acquisition_type="Spiral Acquisition", kvp=60.0)
    with pytest.raises(CoefficientNotAvailable):
        evaluate_event(
            file_name="synthetic.dcm",
            vendor="GE",
            model="LightSpeed VCT",
            manufacturer_raw="GE MEDICAL SYSTEMS",
            model_raw="LightSpeed VCT",
            index=0,
            acq=acq,
        )


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

    stationary = exclusion_reason(
        _event(acquisition_type="Stationary Acquisition"), resolved=resolved
    )
    assert "the table does not move" in stationary[0]
    assert "accumulates over the dwell" in stationary[0]

    unknown_type = exclusion_reason(
        _event(acquisition_type="Some Future Acquisition"), resolved=resolved
    )
    assert unknown_type[0] == "acquisition type is not one the formula models"

    incomplete = exclusion_reason(_event(pitch=None), resolved=resolved)
    assert incomplete == ("incomplete technique", "missing: pitch")

    # Each exclusion names a distinct reason, so the tally in the paper cannot merge
    # three different situations under one heading as it did until 2026-11-08.
    reasons = {
        exclusion_reason(event, resolved=resolved)[0]
        for event in (
            _event(acquisition_type="Stationary Acquisition"),
            _event(acquisition_type="Sequenced Acquisition", pitch=0.1),
            _event(acquisition_type="Some Future Acquisition"),
        )
    }
    assert len(reasons) == 3, f"three situations collapsed into {reasons}"


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


def test_the_recorded_collimation_is_checked_against_a_measured_duration() -> None:
    """The exposure time the scanner recorded, not a ratio that restates the input.

    The previous version of this check compared the duration implied by the recorded
    collimation with the duration implied by 40 mm, and asserted the ratio exceeded ten.
    Two reviewers pointed out that the ratio is algebraically 40 divided by the recorded
    collimation: it holds for any recorded width below 4 mm and measures nothing.

    DCM 113824 is the event's total exposure time, recorded by the scanner and
    independent of the width it claims, so the width the exposure actually used is
    L * t_rot / (duration * pitch) -- four recorded numbers and no assumption.

    VCT event 5: 468.12 mm, 0.5 s per rotation, pitch 0.97, 12.67 s total. That is
    468.12 * 0.5 / (12.67 * 0.97) = 19.0 mm, against the 2.5 mm the report claims.
    """
    out = beam_width_consistency(_event(exposure_time_total_s=12.67))
    assert out["measured"] is True
    assert out["implied_collimation_mm"] == pytest.approx(
        468.1 * 0.5 / (12.67 * 0.97), rel=1e-9
    )
    assert out["implied_collimation_mm"] == pytest.approx(19.0, abs=0.1)
    assert out["corroborated"] is False, "2.5 mm is not within 10 % of 19 mm"

    # A width the clock agrees with is corroborated, and the arithmetic says so rather
    # than a tolerance chosen to make one case pass.
    agreeing = beam_width_consistency(
        _event(total_collimation_mm=40.0, scanning_length_mm=397.5, pitch=1.0,
               exposure_time_per_rotation_s=0.7, exposure_time_total_s=7.0)
    )
    assert agreeing["implied_collimation_mm"] == pytest.approx(39.75, abs=0.01)
    assert agreeing["corroborated"] is True

    # Without the exposure time there is no measurement, and the result must say so
    # rather than fall back on the ratio that measured nothing.
    silent = beam_width_consistency(_event(exposure_time_total_s=None))
    assert silent["measured"] is False
    assert "implied_collimation_mm" not in silent
    assert "cannot be checked" in silent["note"]


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
        assert _slug(case["acquisition_type"]) in MOVING_TABLE_TYPES
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
