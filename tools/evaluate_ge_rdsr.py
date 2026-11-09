"""Check the open-table arithmetic on GE against the scanners' own dose reports.

The accuracy arm built from the public archive (``tools/evaluate_open_fallback.py``)
has no GE case, for a reason that is the subject of the paper rather than an oversight:
GE records CTDIvol in no sampled image header, so the archive offers nothing to compare
a GE prediction against. The reviewing section editor asked for either phantom scanning
or a source that carries RDSR for GE.

This is that source, and it was already in hand. Layer 1 parses three GE CT Radiation
Dose SRs, and an RDSR carries the scanner's **own** CTDIvol per irradiation event
alongside the technique that produced it. Reading the technique out of the report and
predicting the CTDIvol from the open coefficient table therefore tests, without any
phantom, the arithmetic that turns recorded metadata into a dose index on GE -- and only
that; see below and ``provenance`` in the output for what it does not test.

What this establishes, and what it does not
-------------------------------------------
It tests the **arithmetic from recorded metadata to a dose index**: the model match, the
tube-potential and phantom lookup, mAs per rotation from current x time, and the division
by pitch. It does *not* test the choice of collimation row or bowtie filter, which are
varied over -- that variation is what the reported range is. It does *not* test the **metadata-gap recovery path**, because the technique here
is read from a structured report rather than reconstructed from a de-identified image
header. Nor does it test the **vendor's dosimetry**: the reference is the CTDIvol the
console displayed and the open table is a survey of console figures from the same factory
calibrations, so the two are not independent sources -- see
``provenance.the_reference_and_the_coefficients_share_a_source`` in the output. These are
different claims and the manuscript must not merge them. This docstring claimed the wider
"tests the dose model" until a reviewer found it on 2026-11-08, contradicting the
provenance block in the same file.

Eligibility is decided by named rules, applied before any prediction is attempted, so
that the comparison set is defined rather than being whatever the prediction happened
not to fail on. Every exclusion is counted and reported with its reason.

The decisive rule is **that the table moves**, which is not the same as helical. The
formula

    CTDIvol = CTDIw(model, kVp, collimation, phantom, bowtie) * (mAs_rot / 100) / pitch

models the dose index of one rotation's worth of output spread along the table feed. A
*spiral* acquisition feeds continuously; a *sequenced* acquisition is axial
step-and-shoot and the table advances **between** rotations, so the formula applies there
too with pitch = increment / (N.T). In a *stationary* or cine acquisition the table does
not move and the scanner accumulates CTDIvol over the whole dwell, so ``exposure time per
rotation`` no longer gives the exposure that produced the recorded figure. A second rule
catches the geometry rather than the label: four of the GE events record a pitch of 0.100
over a scanning length of 0.0 mm, nominally sequenced but actually a cine series at one
couch position. Predicting any of these would compare a single-rotation model against an
accumulated measurement and report the difference as model error.

What the arm reports is a **range**, not an error per event, because two inputs are not
determined by the report: the bowtie filter, which DICOM never records, and the total
collimation, which four of five events record in a way their own total exposure time
(DCM 113824) contradicts. That exposure time, with the scanning length (113825, the table
travel over the whole tube loading), gives the width the scan actually used. It refutes the
recorded width where the two disagree by 86 % to 87 %, but cannot name which tabulated row
the scan used, so where it refutes the width is undetermined and the range runs over every
tabulated row. The narrower span that follows from applying the same margin to the
candidate rows is published beside it, because the report does not determine which reading
to take. See :func:`_band_over_admitted_widths`.

Usage::

    CTDOSE_RDSR_SAMPLES=/path/to/samples python tools/evaluate_ge_rdsr.py
    python tools/evaluate_ge_rdsr.py /path/to/samples --out results

Writes ``results/ge_rdsr_accuracy.json``. As in Layer 1, the sample objects are
third-party (OpenREM and PixelMed, GPL-3.0) and are **not** vendored into this MIT
repository; only the derived measurements are written.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from pathlib import Path
from typing import Any

from ctdose_core.ctdi_table import (
    BOWTIES,
    CoefficientNotAvailable,
    lookup,
    models,
    provenance,
    resolve_model,
)
from ctdose_core.metrics import Acquisition, estimate_ctdivol_open
from ctdose_core.rdsr import RdsrAcquisition, phantom_cm, read_rdsr

#: Acquisition types the formula models, from CID 10013 "CT Acquisition Type".
#:
#: Two types qualify, not one. A spiral acquisition spreads one rotation's output along a
#: continuous table feed. A **sequenced** acquisition is axial step-and-shoot: the table
#: *does* move, between rotations rather than during them, and its CTDIvol is
#: CTDIw x (N.T / increment), which is this formula with pitch = increment / (N.T). An
#: earlier version of this file put "sequenced acquisition" among the types with no table
#: feed and excluded them for accumulating over a dwell, which is not what they do. Two
#: independent reviewers found it on 2026-11-08. It cost one admissible event, and that
#: event turned out to be the best evidence in the arm.
MOVING_TABLE_TYPES = ("spiral acquisition", "sequenced acquisition")

#: Types that irradiate with the table stationary. Their recorded CTDIvol accumulates over
#: the dwell, so mAs per rotation does not describe the exposure behind it.
STATIONARY_TYPES = (
    "stationary acquisition",
    "constant angle acquisition",
    "free acquisition",
)

#: A pitch at or below this cannot be carrying the table, **when nothing moved**. Four
#: events in the sample record 0.100 with a scanning length of 0.0 mm: nominally
#: sequenced, actually a cine series at one couch position. They are excluded for
#: degenerate geometry rather than for their type, because the type is not what
#: disqualifies them.
#:
#: Both conditions are needed. Until 2026-11-08 the rule fired on the pitch alone, which
#: is wider than this rationale -- a genuine low-pitch spiral over a real scanning length
#: would have been excluded as a dwell. A reviewer pointed out that a positive low pitch
#: does not by itself establish stationary geometry.
DEGENERATE_PITCH = 0.2


def _slug(text: str) -> str:
    return " ".join(str(text).lower().split())


def technique_complete(a: RdsrAcquisition) -> list[str]:
    """Names of the technique fields the reconstruction needs and the event lacks."""
    needed = {
        "tube potential": a.kvp,
        "mean tube current": a.mean_tube_current_ma,
        "exposure time per rotation": a.exposure_time_per_rotation_s,
        "pitch": a.pitch,
        "total collimation": a.total_collimation_mm,
    }
    missing = [name for name, value in needed.items() if value is None or value <= 0]
    if phantom_cm(a.phantom_type) is None:
        missing.append("CTDIw phantom code")
    return missing


def exclusion_reason(
    acq: RdsrAcquisition,
    *,
    resolved: tuple[str, str] | None,
    refusal: str = "",
) -> tuple[str, str]:
    """Why this event is not in the comparison, or ``("", "")`` if it is.

    The rules are applied in a fixed order and each returns its own reason, so an event
    is counted against the first rule it fails rather than against whichever check
    happened to raise. Having this as one function, outside the file loop, is also what
    lets the tests drive the rule set directly on synthetic events.
    """
    detail = (
        f"acquisition type {acq.acquisition_type!r}, pitch {acq.pitch!r}, "
        f"scanning length {acq.scanning_length_mm!r} mm"
    )
    # Not merely present: positive. A zero or negative value is not a dose and cannot
    # be a reference, and it would divide the relative error. A reviewer pointed out on
    # 2026-11-08 that a negative value passed eligibility.
    if acq.ctdivol is None or float(acq.ctdivol) <= 0:
        return (
            "no recorded CTDIvol to compare against",
            "" if acq.ctdivol is None else f"recorded {float(acq.ctdivol):g} mGy",
        )
    if resolved is None:
        return ("scanner not in the open coefficient database", refusal)

    kind = _slug(acq.acquisition_type)
    if kind in STATIONARY_TYPES:
        return (
            "the table does not move, so the recorded CTDIvol accumulates over the "
            "dwell and mAs per rotation does not describe the exposure behind it",
            detail,
        )
    if kind not in MOVING_TABLE_TYPES:
        return ("acquisition type is not one the formula models", detail)

    # Type alone is not enough: a sequenced acquisition at pitch 0.1 over zero length is
    # a cine series wearing the label of a step-and-shoot.
    #
    # Both conditions are required, and that is a correction made on 2026-11-08. The
    # rule excluded any event at a pitch of 0.2 or less whatever its scanning length,
    # which is wider than its own stated rationale: a genuine low-pitch spiral over a
    # real length would have been thrown out as a dwell. A reviewer pointed out that a
    # positive low pitch does not establish stationary geometry. It is the pair -- a
    # pitch that cannot carry the table plus no distance travelled -- that does. The
    # four events this excludes in the sample record pitch 0.100 over 0.0 mm and are
    # unaffected.
    # A missing scanning length and a recorded zero are different things, and conflating
    # them was a defect until 2026-11-08: an absent DCM 113825 was reported as "no
    # scanning length, so the table did not move", which a missing attribute is no
    # evidence of. A reviewer pointed it out. The CTDIvol formula does not use the
    # scanning length at all -- only the beam-width cross-check does -- so an otherwise
    # complete event without it is admitted, and the cross-check reports itself as not
    # measured.
    # Zero *or negative*: a negative recorded length is contradictory geometry too, and
    # treating only an exact zero as recorded-no-travel admitted it. A reviewer found
    # that on 2026-11-08, the same round the positivity guard below went in.
    recorded_zero_distance = (
        acq.scanning_length_mm is not None and acq.scanning_length_mm <= 0
    )
    # Strictly positive: bool() accepts a negative length, which would let
    # contradictory geometry through as proof that the table moved.
    travelled = acq.scanning_length_mm is not None and acq.scanning_length_mm > 0
    degenerate_pitch = acq.pitch is not None and acq.pitch <= DEGENERATE_PITCH

    # A pitch at or below DEGENERATE_PITCH cannot be carrying the table, and that stands
    # whether or not the report says how far it went: only a *recorded positive* distance
    # proves the table moved. Requiring the zero to be recorded, as this rule did for one
    # round, let a pitch-0.100 cine series with no DCM 113825 through to be predicted with
    # the single-rotation formula -- a tenfold error reported as model error. A reviewer
    # found the hole. The original objection stands too: a low pitch over a real distance
    # is a slow feed, and is admitted.
    if degenerate_pitch and not travelled:
        return (
            f"degenerate geometry: a pitch of {acq.pitch:g} cannot carry the table, and "
            f"no positive scanning length is recorded to show that it did",
            detail,
        )
    if recorded_zero_distance:
        # A recorded zero travel with a pitch that could carry the table is internally
        # contradictory rather than a dwell, so it gets its own reason. Until a reviewer
        # pointed it out on 2026-11-08 this branch caught everything the one above does,
        # which made the pitch half of that rule decide nothing despite the comment
        # saying both halves were required.
        #
        # With no recorded pitch at all it is the technique that is incomplete, and the
        # formula cannot run whatever the geometry says. Formatting a missing pitch here
        # crashed the run -- "{None:g}" raises -- instead of counting the exclusion, and
        # both reviewers found the path on the same round.
        if acq.pitch is None:
            return (
                "incomplete technique",
                "missing: pitch; and the recorded scanning length is 0",
            )
        return (
            f"contradictory geometry: a recorded scanning length of 0 at a pitch of "
            f"{acq.pitch:g}, which would carry the table",
            detail,
        )

    missing = technique_complete(acq)
    if missing:
        return ("incomplete technique", "missing: " + ", ".join(missing))
    return ("", "")


#: There was a ``CORROBORATION_TOLERANCE = 0.10`` here, and it decided two things: whether
#: a recorded collimation counted as corroborated, and which tabulated rows the
#: measurement admitted. The second was the problem -- one event sits 9.9 % from a
#: tabulated row, so a 10 % rule and a 9 % rule gave different spans. The replacement
#: (:data:`REFUTATION_MARGIN`) decides only the first, where the data leave no room for
#: doubt: 0.6 % against 86-87 %.


def beam_width_consistency(acq: RdsrAcquisition) -> dict[str, Any]:
    """Does the scanner's own exposure time agree with the collimation it recorded?

    The reports record 2.5 or 5.0 mm in DCM 113827 "Nominal Total Collimation Width" on
    64-slice scanners. An earlier version of this function argued that this cannot be a
    beam width because the implied scan would run for 97 s, and compared that against the
    duration at 40 mm. Two reviewers pointed out, correctly, that the ratio of those two
    durations is algebraically 40 divided by the recorded collimation: it restates the
    recorded number and measures nothing. The argument had no empirical content.

    The report carries the measurement that settles it. DCM 113824 is the event's *total*
    exposure time, recorded by the scanner and independent of the collimation it claims.
    For a moving table,

        duration = (L / (collimation * pitch)) * t_rot

    so the collimation the exposure actually used is

        implied collimation = L * t_rot / (duration * pitch)

    That is four recorded numbers and no assumption.

    Where it agrees, the recorded collimation stands. Where it does not, the recorded
    field is refuted -- and the measurement cannot name a replacement, so the width
    becomes undetermined; :func:`_band_over_admitted_widths` gives the two readings
    that follow and why the arm publishes both instead of choosing.

    Nothing is corrected here. The implied width is reported beside the recorded one.
    """
    length = float(acq.scanning_length_mm or 0.0)
    pitch = float(acq.pitch or 0.0)
    t_rot = float(acq.exposure_time_per_rotation_s or 0.0)
    recorded = float(acq.total_collimation_mm or 0.0)
    total = acq.exposure_time_total_s

    out: dict[str, Any] = {
        "scanning_length_mm": length,
        "recorded_collimation_mm": recorded,
        "exposure_time_total_s": total,
        "formula": "implied collimation = L * t_rot / (total exposure time * pitch)",
        "measured": total is not None and total > 0 and length > 0 and pitch > 0,
    }
    if not out["measured"]:
        out["note"] = (
            "DCM 113824 is absent or the geometry is degenerate, so the recorded "
            "collimation cannot be checked against a measured duration here"
        )
        return out

    implied = length * t_rot / (float(total) * pitch)
    out["implied_collimation_mm"] = implied
    out["relative_difference"] = (recorded - implied) / implied
    # No figure for how precisely the width is known is published here, and that is
    # deliberate. Any such figure would be dominated by the rotation time, which GE
    # records as a discrete console setting rather than a rounded continuum, so it would
    # be an assumption dressed as a measurement -- and it would then decide which
    # tabulated row the arm used. Two reviewers refused exactly that on 2026-11-08.
    #
    # What the measurement supports is refutation, and the margin is not delicate: the
    # four refuted events differ from their measured width by 86 % to 87 % and the
    # corroborated one by 0.6 %, so any line between them gives the same answer.
    out["corroborated"] = abs(recorded - implied) <= REFUTATION_MARGIN * implied
    out["refutation_margin"] = REFUTATION_MARGIN
    out["margin_note"] = (
        "used only to decide whether the recorded width is contradicted, never to "
        "choose a tabulated row; see _band_over_admitted_widths"
    )
    out["predicted_duration_at_recorded_collimation_s"] = (
        length / (recorded * pitch) * t_rot if recorded else None
    )
    return out


def assumption_envelope(
    vendor: str,
    model: str,
    *,
    kvp: float,
    phantom: int,
    mas_per_rotation: float,
    pitch: float,
    recorded: float,
) -> dict[str, Any]:
    """Every prediction the unrecorded choices allow, not one of them.

    Two inputs are not determined by the report. **The bowtie filter is never recorded in
    DICOM**, and where the filter conventional for the phantom is not tabulated for a
    model the code must pick among those that are. For the Optima 660 at the 32 cm
    phantom the conventional 'medium' is absent and 'small' and 'large' are equally far
    from it, so the tie was being broken by the order of a tuple -- which chose the head
    filter for a body phantom and, with it, the headline. Reported as a single number,
    that headline said all four events fell within 20 %; with the body filter two of them
    do not.

    **The collimation is recorded but can be refuted** by the exposure time
    (:func:`beam_width_consistency`), and where it is refuted the width is an assumption
    too.

    So the result of this arm is an envelope over the tabulated filters and the tabulated
    widths, with each combination's error reported. A single number here would be a
    choice presented as a measurement.
    """
    match = next((m for m in models() if m.vendor == vendor and m.model == model), None)
    if match is None:
        return {}
    rows = [r for r in match.rows if r["phantom_cm"] == phantom]
    bowties = sorted({r["bowtie"] for r in rows}, key=BOWTIES.index)
    collimations = sorted({r["collimation_mm"] for r in rows})

    grid: list[dict[str, Any]] = []
    for bowtie in bowties:
        for coll in collimations:
            try:
                hit = lookup(
                    vendor, model, kvp=kvp, collimation_mm=coll,
                    phantom_cm=phantom, bowtie=bowtie,
                )
            except CoefficientNotAvailable:
                continue
            # Every cell of this grid must be a measured row, not a resolved one. A
            # substituted bowtie or collimation is caught by comparing the keys, but a
            # substituted *tube potential* is not: CtdiLookup.kvp echoes the request and
            # a kVp that is not tabulated is interpolated, recorded only in
            # ``assumptions``. So any assumption at all disqualifies the cell. Without
            # this, a grid at an untabulated kVp would fill with interpolated values and
            # present them as the span of what the table measures.
            if hit.bowtie != bowtie or hit.collimation_mm != coll or hit.assumptions:
                continue
            grid.append({
                "bowtie": bowtie,
                "collimation_mm": coll,
                "kvp": float(kvp),
                "exact_row": True,
                "nctdi_w_mgy_per_100mas": hit.nctdi_w_mgy_per_100mas,
                "estimated_ctdivol_mgy": (
                    hit.nctdi_w_mgy_per_100mas * (mas_per_rotation / 100.0) / pitch
                ),
                "is_nearest_to_recorded": coll == _nearest_tabulated(recorded, collimations),
            })
    return {
        "bowties_tabulated": bowties,
        "collimations_tabulated": collimations,
        "conventional_bowtie_for_phantom": "small" if phantom == 16 else "medium",
        "conventional_bowtie_is_tabulated": (
            ("small" if phantom == 16 else "medium") in bowties
        ),
        "grid": grid,
    }


def _nearest_tabulated(value: float, candidates: list[float]) -> float:
    return min(candidates, key=lambda c: (abs(c - value), c))


def table_profile(vendor: str, model: str, *, kvp: float, phantom: int) -> dict[str, Any]:
    """The table's coefficient at every tabulated collimation for this technique.

    The GE reports record a 2.5 or 5.0 mm total collimation on 64-slice scanners, while
    the narrowest beam tabulated for those two models is 10 mm. Whether that is a
    genuinely narrow beam or a vendor writing a single detector row into the
    total-collimation field changes the prediction substantially, so the alternatives are
    printed rather than argued about.

    The narrowest row is not 10 mm for every GE model: Revolution CT ES is tabulated at
    5 mm. A report from that scanner recording 5 mm would match exactly, raise no
    assumption, and so hide the same recording defect behind an apparently clean lookup.
    That is why ``beam_width_consistency`` tests the recorded width against the scan
    duration rather than against whether the table happened to have a matching row.
    """
    match = next((m for m in models() if m.vendor == vendor and m.model == model), None)
    if match is None:
        return {}
    out: dict[str, Any] = {}
    colls = sorted({r["collimation_mm"] for r in match.rows if r["phantom_cm"] == phantom})
    for coll in colls:
        try:
            hit = lookup(
                vendor, model, kvp=kvp, collimation_mm=coll, phantom_cm=phantom
            )
        except CoefficientNotAvailable:
            continue
        out[f"{coll:g}mm"] = {
            "nctdi_w_mgy_per_100mas": hit.nctdi_w_mgy_per_100mas,
            "bowtie": hit.bowtie,
        }
    return out


def evaluate_event(
    *,
    file_name: str,
    vendor: str,
    model: str,
    manufacturer_raw: str,
    model_raw: str,
    index: int,
    acq: RdsrAcquisition,
) -> dict[str, Any]:
    """Predict one event's CTDIvol and compare it with the figure the scanner recorded."""
    recorded = float(acq.ctdivol)
    phantom = phantom_cm(acq.phantom_type)
    mas_rot = float(acq.mean_tube_current_ma) * float(acq.exposure_time_per_rotation_s)

    # slice_thickness_mm and scan_range_mm are validated by Acquisition but are not
    # inputs to estimate_ctdivol_open; check_formula_inputs() below proves that rather
    # than asserting it, so that a future change to the formula cannot silently start
    # depending on a value this script invented.
    geometry = Acquisition(
        mas_per_rotation=mas_rot,
        collimation_mm=float(acq.total_collimation_mm),
        pitch=float(acq.pitch),
        slice_thickness_mm=float(acq.total_collimation_mm),
        scan_range_mm=float(acq.scanning_length_mm or 0.0),
        kvp=float(acq.kvp),
    )
    est = estimate_ctdivol_open(geometry, vendor=vendor, model=model, phantom_cm=phantom)
    estimated = float(est)
    assumptions = [n for n in est.notes if not n.startswith("reconstructed because")]

    case: dict[str, Any] = {
        "file": file_name,
        "event_index": index,
        "manufacturer_raw": manufacturer_raw,
        "model_raw": model_raw,
        "table_vendor": vendor,
        "table_model": model,
        "acquisition_type": acq.acquisition_type,
        "protocol": acq.protocol,
        "phantom_cm": phantom,
        "kvp": float(acq.kvp),
        "mean_tube_current_ma": float(acq.mean_tube_current_ma),
        "exposure_time_per_rotation_s": float(acq.exposure_time_per_rotation_s),
        "mas_per_rotation": mas_rot,
        "pitch": float(acq.pitch),
        "recorded_collimation_mm": float(acq.total_collimation_mm),
        "scanning_length_mm": acq.scanning_length_mm,
        "modulation_type": acq.modulation_type,
        "recorded_ctdivol_mgy": recorded,
        "estimated_ctdivol_mgy": estimated,
        "percent_error": 100.0 * (estimated - recorded) / recorded,
        "table_ctdiw_mgy_per_100mas": est.inputs["CTDIw_mGy_per_100mAs"],
        "table_collimation_mm": est.inputs["table_collimation_mm"],
        "table_kvp": est.inputs["table_kvp"],
        "table_bowtie": est.inputs["table_bowtie"],
        "exact_table_match": not assumptions,
        "assumptions": assumptions,
        "implied_nctdi_w_mgy_per_100mas": recorded * float(acq.pitch) / (mas_rot / 100.0),
        "table_at_every_collimation": table_profile(
            vendor, model, kvp=float(acq.kvp), phantom=phantom
        ),
        "beam_width_consistency": beam_width_consistency(acq),
        "assumption_envelope": _envelope_errors(
            assumption_envelope(
                vendor, model, kvp=float(acq.kvp), phantom=phantom,
                mas_per_rotation=mas_rot, pitch=float(acq.pitch),
                recorded=float(acq.total_collimation_mm),
            ),
            recorded,
        ),
    }
    # An event with no exactly tabulated cell has no span to report, so it belongs in
    # the exclusions with a counted reason rather than in the comparison with an empty
    # band. Raising the lookup's own error puts it on the path the file loop already
    # handles; before 2026-11-08 it reached _headline and crashed the run.
    if not case["assumption_envelope"].get("grid"):
        raise CoefficientNotAvailable(
            f"no exactly tabulated row for {model} at {float(acq.kvp):g} kVp and the "
            f"{phantom} cm phantom, so there is no span to report"
        )

    # The band the manuscript quotes for this row. The filter always varies, because
    # DICOM never records it. The width is held at the recorded value where the measured
    # exposure time agrees with it and the table holds it, and varies over every
    # tabulated row otherwise -- the measurement refutes the recorded width but does not
    # pick a replacement.
    case["reported_band"] = _band_over_admitted_widths(
        case["assumption_envelope"], case["beam_width_consistency"]
    )
    # The condition under which this event's arithmetic counts as reproduced, stated so
    # that the arm can fail, and evaluated for every reading so that stating it does not
    # smuggle in a choice between them.
    #
    # It asks whether the best-agreeing admissible combination comes within
    # ARITHMETIC_TOLERANCE of the recorded value. The first version of this condition
    # asked whether the span contained zero, which both reviewers refused on 2026-11-08:
    # the grid holds discrete cells, so a span straddling zero does not mean any cell
    # reproduces the reference, and the condition was applied to the wide reading alone.
    case["arithmetic_reproduced"] = {
        name: _best_admissible_cell(case["assumption_envelope"], widths)
        for name, widths in _readings(case["reported_band"]).items()
    }
    return case


def _envelope_errors(envelope: dict[str, Any], recorded_ctdivol: float) -> dict[str, Any]:
    """Attach each cell's error, and the span, to the assumption grid.

    An empty grid is possible: every cell is required to be an exact tabulated row, so an
    event at an untabulated tube potential leaves nothing. Until 2026-11-08 this returned
    the envelope with no ``percent_error_min``, and :func:`_headline` then raised a
    KeyError reading it -- the whole run crashed instead of excluding that event with a
    counted reason, which is the opposite of what this script claims to do. A reviewer
    found the path. The span is now written as empty and :func:`evaluate_event` refuses
    the event.
    """
    if not envelope.get("grid"):
        envelope["n_combinations"] = 0
        envelope["no_exactly_tabulated_cell"] = True
        return envelope
    for cell in envelope["grid"]:
        cell["percent_error"] = (
            100.0 * (cell["estimated_ctdivol_mgy"] - recorded_ctdivol) / recorded_ctdivol
        )
    errors = [c["percent_error"] for c in envelope["grid"]]
    envelope["percent_error_min"] = min(errors)
    envelope["percent_error_max"] = max(errors)
    envelope["n_combinations"] = len(errors)
    envelope["any_within_20_percent"] = any(abs(e) <= 20.0 for e in errors)
    envelope["all_within_20_percent"] = all(abs(e) <= 20.0 for e in errors)
    return envelope


#: How far the recorded and measured widths may differ and still be called consistent.
#:
#: This is used **only to refute**, never to select a tabulated row, and that asymmetry
#: is the point. Refuting needs no precision: the four refuted events differ from their
#: measured width by 86 % to 87 %, orders of magnitude beyond any reading of how well the
#: inputs are known, while the corroborated one differs by 0.6 %. Selecting a row would
#: need the measurement to be good to a few per cent, and nothing in the report
#: establishes that.
#:
#: The arm tried four ways to select a row and every one rested on a chosen number: a
#: nearest-row rule with a 10 % tolerance; a floor from a misreading of DCM 113825; an
#: exact bracket, discontinuous at the scale of the rounding; and a tolerance propagated
#: from declared input precisions whose dominant term -- a rotation time to +/- 0.05 s --
#: is itself an assumption, since GE rotation times are discrete console settings and not
#: values rounded from a continuum. Two reviewers refused the last of these on
#: 2026-11-08, and they were right: there is no rule here that is not a parameter.
#:
#: The four, kept because each changed the reported spans:
#:
#: 1. The nearest row to the recorded width, with the bowtie tie broken by the order
#:    of a module-level tuple -- the head filter for a body phantom. The headline then
#:    read "+11.6 %, all four within 20 %".
#: 2. Every tabulated row, while the same measurement was used to reject the recorded
#:    one: two incompatible positions, and both values outside 20 % came from a 10 mm
#:    row the measurement refutes threefold.
#: 3. Every row at or above the measured width, reading DCM 113825 as the planned
#:    range so that over-ranging made it a floor. Table D-1 contradicts that reading.
#: 4. The two rows bracketing it, first exactly -- discontinuous at the scale of the
#:    rounding, since 39.75 mm against a tabulated 40.0 mm then admitted the 20 mm row
#:    the same measurement refutes by half -- and then within a tolerance propagated
#:    from declared input precisions. That version also called the bracket a bound on
#:    the coefficient, on the strength of its endpoints descending, which says nothing
#:    about the interior.
#:
#: So the arm does not select. Where the recorded width is refuted, the width is simply
#: undetermined, and the span runs over every tabulated row -- which is what an
#: undetermined input means.
REFUTATION_MARGIN = 0.25

#: How close the best-agreeing admissible combination must come to the recorded CTDIvol
#: for the arithmetic to count as reproduced, as a fraction.
#:
#: A round figure, set above two quantities and claimed to follow from neither.
#:
#: The rounding of the two numbers compared is small: the recorded CTDIvol is written to
#: 0.01 mGy, which is 0.02 % to 0.15 % of these values, and the tabulated coefficients
#: carry three significant figures, about 0.05 %. The best admissible cell comes to 0.00 %
#: to 0.50 %, so the residual is an order of magnitude larger than the rounding -- it is
#: how far the nearest tabulated combination sits from the technique actually used, which
#: is a property of the table's grid rather than of arithmetic.
#:
#: An earlier comment here said the tolerance was "set by the rounding", which a reviewer
#: refused on 2026-11-08: the rounding budget does not reach 0.5 %. One per cent is
#: therefore stated as a round threshold comfortably above both, and every event's actual
#: best-cell error is published
#: (``cases[].arithmetic_reproduced[reading].percent_error``) so that a reader can apply a
#: stricter one.
ARITHMETIC_TOLERANCE = 0.01


def _reading_aggregate(cases: list[dict[str, Any]], reading: str) -> dict[str, Any]:
    """One reading's verdict across the events, counting what it cannot answer for.

    A reading with no admissible combination for an event offers no prediction there, so
    it has no best-cell error. Taking a maximum over the others would report a worst case
    across a subset without saying so, and reading the absent error crashed the run.
    """
    rows = [
        c["arithmetic_reproduced"][reading]
        for c in cases
        if reading in c["arithmetic_reproduced"]
    ]
    answered = [r for r in rows if r.get("n")]
    return {
        "n_events": len(rows),
        "n_events_this_reading_can_answer": len(answered),
        "n_events_incompatible_with_this_reading": len(rows) - len(answered),
        "all_within_tolerance": bool(rows) and all(r["within_tolerance"] for r in rows),
        "worst_best_cell_percent_error": max(
            (abs(r["percent_error"]) for r in answered), default=None
        ),
        "worst_case_is_over_all_events": len(answered) == len(rows),
    }


def _readings(band: dict[str, Any]) -> dict[str, list[float]]:
    """Each reading this band publishes, as the set of collimations it admits."""
    out: dict[str, list[float]] = {}
    if band.get("n"):
        out["as_reported"] = band["widths_mm"]
    narrowed = band.get("also_contradicted_removed")
    if narrowed is not None:
        # Including the empty case, with its empty width list. Omitting it reported a
        # reading that cannot be satisfied as if it did not exist; a reviewer pointed
        # that out on 2026-11-08. _best_admissible_cell returns n=0 and
        # incompatible=True for it.
        out["also_contradicted_removed"] = narrowed.get("widths_mm") or []
    return out


def _best_admissible_cell(
    envelope: dict[str, Any], widths: list[float]
) -> dict[str, Any]:
    """The admissible combination that comes closest to the recorded value.

    Reported rather than reduced to a verdict alone, so that a reader sees how close the
    best combination came and which one it was -- a 0.5 % agreement at one bowtie and
    collimation is a different thing from a span that merely straddles zero.
    """
    cells = [g for g in envelope.get("grid") or [] if g["collimation_mm"] in widths]
    if not cells:
        return {
            "n": 0,
            "incompatible": True,
            "within_tolerance": False,
            "tolerance_percent": 100.0 * ARITHMETIC_TOLERANCE,
            "note": (
                "no admissible combination exists under this reading, so it offers no "
                "prediction to compare -- not a pass"
            ),
        }
    best = min(cells, key=lambda g: abs(g["percent_error"]))
    return {
        "n": len(cells),
        "bowtie": best["bowtie"],
        "collimation_mm": best["collimation_mm"],
        "percent_error": best["percent_error"],
        "within_tolerance": abs(best["percent_error"]) <= 100.0 * ARITHMETIC_TOLERANCE,
        "tolerance_percent": 100.0 * ARITHMETIC_TOLERANCE,
    }


def _band_over_admitted_widths(
    envelope: dict[str, Any], consistency: dict[str, Any]
) -> dict[str, Any]:
    """The span this event supports, over the choices the report leaves open.

    Where the exposure time agrees with the recorded collimation, that width stands and
    the filter is the only assumption: one span.

    Where it refutes the recorded collimation, the width is not determined by the report
    and **two readings are defensible, with nothing in the report to choose between
    them**:

    ``collimation_undetermined_within_the_table``
        Every row the table holds stands -- 10, 20 and 40 mm here -- because the
        report's only statement about the width has been refused and the measurement
        is not precise enough to name a replacement. This is not the width being
        unconstrained: the measured 34.5 mm lies between two tabulated rows and the
        arm cannot interpolate, so the span is over what the table offers, not over
        what the scanner could have used.
    ``rows_the_measurement_also_contradicts_removed``
        The same margin that refused the recorded width is applied to every candidate, so
        a row further from the measured width than that margin goes too. This is the
        consistent reading -- a measurement decisive against 5.0 mm is decisive against
        10 mm -- but on this data it leaves exactly one row every time, so the margin is
        selecting, and the span then depends on where the margin is set.

    Both are published: the wider one as ``percent_error_min``/``_max``, the narrower
    beside it under ``also_contradicted_removed``. Neither is the safe one. The wide
    reading treats the measurement as decisive against the width the report states and
    indecisive against every other candidate, which is a position about the
    measurement's precision as surely as the narrow reading is -- a reviewer made that
    point on 2026-11-08 and it is right. The wide span is given first because it is the
    weaker claim about the result, not because it assumes less about the measurement.
    Four earlier versions of this arm each picked one rule and each rested on a number
    chosen rather than measured; the choice is reported here instead of made.
    """
    grid = envelope.get("grid") or []
    if not grid:
        return {"n": 0}

    tabulated = sorted({g["collimation_mm"] for g in grid})
    recorded = consistency["recorded_collimation_mm"]

    def span(widths: list[float]) -> dict[str, Any]:
        errors = [g["percent_error"] for g in grid if g["collimation_mm"] in widths]
        return {
            "widths_mm": widths,
            "n": len(errors),
            "percent_error_min": min(errors),
            "percent_error_max": max(errors),
        }

    if not consistency.get("measured"):
        whole = span(tabulated)
        return {
            **whole,
            "assumed": "bowtie and collimation",
            "recorded_collimation_is_admitted": True,
            "how_the_width_was_resolved": "no measured exposure time",
            "because": (
                "the beam-width cross-check cannot run on this event -- it needs the "
                "total exposure time (DCM 113824), the scanning length (113825) and a "
                "positive pitch, and at least one is absent -- so the recorded width "
                "cannot be checked and every tabulated row stands. See "
                "beam_width_consistency.note for which was missing"
            ),
        }

    implied = consistency["implied_collimation_mm"]
    relative = abs(recorded - implied) / implied if implied else float("inf")

    if relative <= REFUTATION_MARGIN:
        # The recorded width stands. The lookup uses the nearest tabulated row to it,
        # which is the recorded width itself unless the table lacks that width -- and a
        # substitution is written onto the case as an assumption either way.
        exact_rows = [c for c in tabulated if abs(c - recorded) < 1e-9]
        # Where the table does not hold the recorded width, the coefficient is a choice
        # the report does not settle -- exactly as for a refuted width -- so every
        # tabulated row stays. Two earlier attempts at this branch were refused on
        # 2026-11-08: substituting the nearest row (which hides the choice) and then the
        # rows either side (which is no better evidenced than any other restriction). No
        # event here reaches it, since the one corroborated width is 40.0 mm and
        # tabulated.
        admitted = exact_rows or tabulated
        exact = bool(exact_rows)
        out = span(admitted)
        out.update(
            {
                "assumed": (
                    "bowtie only" if exact else "bowtie and collimation"
                ),
                "recorded_collimation_is_admitted": True,
                "recorded_collimation_is_tabulated": exact,
                "recorded_vs_measured_relative_difference": relative,
                "refutation_margin_used": REFUTATION_MARGIN,
                "how_the_width_was_resolved": "recorded width stands",
                "because": (
                    f"the exposure time gives {implied:.1f} mm against a recorded "
                    f"{recorded:.1f} mm, agreeing to {100 * relative:.1f} %, so the "
                    f"recorded width stands"
                    + (
                        " and only the filter is assumed"
                        if exact
                        else f" -- but the table has no {recorded:.1f} mm row, so the "
                        f"coefficient is still a choice the report does not settle and "
                        f"every tabulated row stays: "
                        f"{', '.join(f'{c:.0f}' for c in admitted)} mm"
                    )
                ),
            }
        )
        return out

    # Refuted. Both readings, neither privileged.
    # No "or tabulated" fallback here. Where no tabulated row survives the margin, the
    # measurement and the table are incompatible under this reading, and restoring every
    # row would reverse the rule rather than apply it -- which is what this line did
    # until a reviewer found it on 2026-11-08. The frozen five events never reach the
    # branch, which is why only review caught it.
    consistent = [
        c for c in tabulated if abs(c - implied) <= REFUTATION_MARGIN * implied
    ]
    whole = span(tabulated)
    narrowed = (
        span(consistent)
        if consistent
        else {
            "widths_mm": [],
            "n": 0,
            "note_no_row_survives": (
                "no tabulated row is within the margin of the measured width, so under "
                "this reading the measurement and the table are incompatible and the "
                "arm has no span to offer -- read the wider one beside it"
            ),
        }
    )
    out = {
        **whole,
        "assumed": "bowtie and collimation",
        "recorded_collimation_is_admitted": False,
        "recorded_vs_measured_relative_difference": relative,
        "refutation_margin_used": REFUTATION_MARGIN,
        "how_the_width_was_resolved": "undetermined within the table, recorded width refuted",
        "also_contradicted_removed": {
            **narrowed,
            "note": (
                "the consistent reading: the margin that refused the recorded width, "
                "applied to every candidate. It leaves one row here, so the margin is "
                "selecting and this span depends on where it is set -- which is why it "
                "is reported beside the wider one rather than instead of it"
            ),
        },
        "because": (
            f"the exposure time gives {implied:.1f} mm against a recorded "
            f"{recorded:.1f} mm, a difference of {100 * relative:.0f} %, so the recorded "
            f"field is not a beam width. It is not precise enough to name which tabulated "
            f"row the scan used, so the width is undetermined and every tabulated row "
            f"stands. Applying the same margin to the candidates instead would leave "
            + (
                f"{', '.join(f'{c:.0f}' for c in consistent)} mm and a span of "
                f"{narrowed['percent_error_min']:+.1f} % to "
                f"{narrowed['percent_error_max']:+.1f} %"
                if consistent
                else "no tabulated row at all, so that reading offers no span here"
            )
            + "; the report does not determine which of the two readings to take, so "
            "both are published"
        ),
    }
    return out


def check_formula_inputs() -> dict[str, Any]:
    """Prove the prediction ignores the two fields this script had to invent.

    ``Acquisition`` requires a slice thickness and a scan range. Neither is recorded in
    an RDSR and neither appears in the CTDIvol formula, so this script passes the
    collimation and the scanning length. If a future change made the estimate depend on
    either, every number here would quietly acquire a made-up input. Perturb them and
    require the estimate not to move.
    """
    base = dict(
        mas_per_rotation=60.0, collimation_mm=20.0, pitch=1.0, kvp=120.0
    )
    a = Acquisition(slice_thickness_mm=5.0, scan_range_mm=100.0, **base)
    b = Acquisition(slice_thickness_mm=0.625, scan_range_mm=700.0, **base)
    va = float(estimate_ctdivol_open(a, vendor="GE", model="LightSpeed VCT", phantom_cm=32))
    vb = float(estimate_ctdivol_open(b, vendor="GE", model="LightSpeed VCT", phantom_cm=32))
    return {
        "question": (
            "does estimate_ctdivol_open depend on slice thickness or scan range, which "
            "an RDSR does not record and this script therefore supplies?"
        ),
        "estimate_with_5mm_slice_100mm_range": va,
        "estimate_with_0.625mm_slice_700mm_range": vb,
        "invariant": va == vb,
    }


def _headline(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """What this arm actually establishes, split by whether the geometry is corroborated.

    Two groups, because they support different claims.

    Where the scanner's own exposure time agrees with the collimation it recorded, the
    only thing still assumed is the bowtie filter, and the error is reported as the range
    over the tabulated filters at the recorded width. Where the exposure time refutes the
    recorded collimation, the width is assumed too, and the honest figure is the range
    over both.

    An earlier version reported one number, "+11.6 %, all four within 20 %". That number
    came from snapping a refuted collimation to the nearest tabulated row and from a
    bowtie tie broken by tuple order. Neither was a measurement.

    The version after it still split the rule in two -- a corroborated geometry fixed the
    width, a refuted one widened over every tabulated row including those the same
    measurement refutes. There is now one rule
    (:func:`_band_over_admitted_widths`), and the two groups differ only in what the
    report leaves open: a corroborated geometry fixes the width, a refuted one leaves
    it undetermined. Each group also carries the span over every tabulated
    collimation and filter, so the narrowing is visible rather than assumed. That key
    was called "if_the_exposure_time_is_disbelieved" until 2026-11-08, which is not
    what it computes: disbelieving DCM 113824 would leave the recorded width standing,
    not every row in play.
    """
    corroborated = [c for c in cases if c["beam_width_consistency"].get("corroborated")]
    refuted = [
        c for c in cases
        if c["beam_width_consistency"].get("measured")
        and not c["beam_width_consistency"].get("corroborated")
    ]
    unmeasured = [c for c in cases if not c["beam_width_consistency"].get("measured")]

    def band(group: list[dict[str, Any]]) -> dict[str, Any]:
        if not group:
            return {"n": 0}
        lows = [c["reported_band"]["percent_error_min"] for c in group]
        highs = [c["reported_band"]["percent_error_max"] for c in group]
        wide_lows = [c["assumption_envelope"]["percent_error_min"] for c in group]
        wide_highs = [c["assumption_envelope"]["percent_error_max"] for c in group]
        assumed = sorted({c["reported_band"]["assumed"] for c in group})
        return {
            "n": len(group),
            "assumed": assumed[0] if len(assumed) == 1 else "; ".join(assumed),
            "worst_case_percent_error_min": min(lows),
            "worst_case_percent_error_max": max(highs),
            "every_event_within_20_percent_for_every_assumption": all(
                abs(v) <= 20.0 for v in lows + highs
            ),
            # The consistent reading, where every event has one: the same margin that
            # refused the recorded width, applied to the candidate rows. Reported beside
            # the wider span, never instead of it, because the margin then selects.
            "if_the_margin_is_applied_to_the_candidates_too": (
                {
                    "assumed": "bowtie, and the width the margin leaves",
                    "n": sum(
                        1
                        for c in group
                        if c["reported_band"].get("also_contradicted_removed", {}).get("n")
                    ),
                    "worst_case_percent_error_min": min(
                        c["reported_band"]["also_contradicted_removed"]["percent_error_min"]
                        for c in group
                        if c["reported_band"].get("also_contradicted_removed", {}).get("n")
                    ),
                    "worst_case_percent_error_max": max(
                        c["reported_band"]["also_contradicted_removed"]["percent_error_max"]
                        for c in group
                        if c["reported_band"].get("also_contradicted_removed", {}).get("n")
                    ),
                }
                if any(
                    c["reported_band"].get("also_contradicted_removed", {}).get("n")
                    for c in group
                )
                else None
            ),
            # Named for what it computes. It was called
            # "if_the_exposure_time_is_disbelieved", which is not what it is: disbelieving
            # DCM 113824 leaves nothing to refute the recorded collimation, so the
            # recorded width would stand and the span would be over the filter alone.
            # This is instead the span with the collimation treated as unknown and the
            # measurement used for nothing -- the widest the table allows. A reviewer
            # named the mislabel on 2026-11-08.
            "over_every_tabulated_collimation_and_filter": {
                "assumed": "bowtie and every tabulated collimation",
                "worst_case_percent_error_min": min(wide_lows),
                "worst_case_percent_error_max": max(wide_highs),
                "every_event_within_20_percent_for_every_assumption": all(
                    abs(v) <= 20.0 for v in wide_lows + wide_highs
                ),
            },
        }

    out = {
        "what_it_means": (
            "Two inputs are not determined by the report: the bowtie filter, which DICOM "
            "never records, and the total collimation, which the report records but its "
            "own total exposure time contradicts for four of five events by 86 % to 87 %. "
            "The result is therefore a range over what those choices allow, not a single "
            "number. The measurement refutes the recorded width but is not precise enough "
            "to name which tabulated row the scan used -- any figure for its precision is "
            "dominated by a rotation time GE records as a discrete console setting -- so "
            "where it refutes, the width is undetermined and every tabulated row stands. "
            "Applying the same margin to the candidate rows instead is the consistent "
            "reading and leaves one row per event, but then the margin is selecting; the "
            "report does not determine which reading to take, so each case publishes both "
            "(cases[].reported_band.also_contradicted_removed)."
        ),
        "geometry_corroborated_by_exposure_time": band(corroborated),
        "geometry_refuted_by_exposure_time": band(refuted),
        "n_corroborated": len(corroborated),
        "n_refuted": len(refuted),
        "n_without_a_measured_exposure_time": len(unmeasured),
    }
    if unmeasured:
        out["no_measured_exposure_time"] = band(unmeasured)
    return out


def _band_over_models(group: list[dict[str, Any]]) -> dict[str, Any]:
    """The worst case of a group's reported bands, with what each one assumed."""
    if not group:
        return {"n": 0}
    lows = [c["reported_band"]["percent_error_min"] for c in group]
    highs = [c["reported_band"]["percent_error_max"] for c in group]
    assumed = sorted({c["reported_band"]["assumed"] for c in group})
    return {
        "n": len(group),
        "assumed": assumed[0] if len(assumed) == 1 else "; ".join(assumed),
        "worst_case_percent_error_min": min(lows),
        "worst_case_percent_error_max": max(highs),
        "every_event_within_20_percent_for_every_assumption": all(
            abs(v) <= 20.0 for v in lows + highs
        ),
    }


def describe_quantity(values: list[float], unit: str) -> dict[str, Any]:
    """Summarise a quantity that is not a percent error.

    :func:`describe` reports ``within_20_percent`` and ``mean_absolute``, which mean
    something for a relative error and nothing for a coefficient in mGy per 100 mAs --
    "within_20_percent: 1.0" there says only that every coefficient is below 20, and a
    reviewer read it on 2026-11-08 as one more accuracy claim. Quantities get their own
    summary, with the unit written on it.
    """
    if not values:
        return {"n": 0, "unit": unit}
    v = sorted(values)
    out: dict[str, Any] = {
        "n": len(v),
        "unit": unit,
        "median": statistics.median(v),
        "mean": statistics.fmean(v),
        "min": v[0],
        "max": v[-1],
    }
    if len(v) > 1:
        out["stdev"] = statistics.stdev(v)
    return out


def describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    v = sorted(values)
    out: dict[str, Any] = {
        "n": len(v),
        "median": statistics.median(v),
        "mean": statistics.fmean(v),
        "min": v[0],
        "max": v[-1],
        "mean_absolute": statistics.fmean(abs(x) for x in v),
        "within_20_percent": sum(1 for x in v if abs(x) <= 20.0) / len(v),
        "within_50_percent": sum(1 for x in v if abs(x) <= 50.0) / len(v),
    }
    if len(v) > 1:
        out["stdev"] = statistics.stdev(v)
    return out


def run(sample_dir: Path) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    for f in sorted(glob.glob(str(sample_dir / "*.dcm"))):
        path = Path(f)
        report = read_rdsr(path)
        if report is None:
            continue
        if "ge" not in _slug(report.manufacturer).replace(" ", "")[:2]:
            continue

        resolved: tuple[str, str] | None
        refusal = ""
        try:
            resolved = resolve_model(report.manufacturer, report.model_name)
        except CoefficientNotAvailable as exc:
            resolved = None
            refusal = str(exc)

        files.append(
            {
                "file": path.name,
                "manufacturer_raw": report.manufacturer,
                "model_raw": report.model_name,
                "n_events": len(report.acquisitions),
                "n_events_with_ctdivol": sum(1 for a in report.acquisitions if a.ctdivol),
                "table_model": f"{resolved[0]} {resolved[1]}" if resolved else None,
                "model_refusal": refusal,
            }
        )

        for i, acq in enumerate(report.acquisitions):
            reason, detail = exclusion_reason(acq, resolved=resolved, refusal=refusal)
            if reason:
                excluded.append(
                    {
                        "file": path.name,
                        "event_index": i,
                        "model_raw": report.model_name,
                        "acquisition_type": acq.acquisition_type,
                        "reason": reason,
                        "detail": detail,
                    }
                )
                continue
            assert resolved is not None  # exclusion_reason guarantees this

            try:
                cases.append(
                    evaluate_event(
                        file_name=path.name,
                        vendor=resolved[0],
                        model=resolved[1],
                        manufacturer_raw=report.manufacturer,
                        model_raw=report.model_name,
                        index=i,
                        acq=acq,
                    )
                )
            except CoefficientNotAvailable as exc:
                excluded.append(
                    {
                        "file": path.name,
                        "event_index": i,
                        "model_raw": report.model_name,
                        "acquisition_type": acq.acquisition_type,
                        "reason": "technique outside the tabulated grid",
                        "detail": str(exc),
                    }
                )

    reasons: dict[str, int] = {}
    for e in excluded:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1

    errors = [c["percent_error"] for c in cases]
    return {
        "provenance": {
            "generated_by": "tools/evaluate_ge_rdsr.py",
            # "reconstruction" was the wrong word here, and a reviewer caught it on
            # 2026-11-08: it names the metadata-gap recovery path, which the key below
            # says is NOT what this arm tests. The question is about the arithmetic.
            "question": (
                "Does the open-table arithmetic reproduce the CTDIvol a GE scanner "
                "computed, given the technique that scanner recorded? The archive "
                "cannot answer this because GE records CTDIvol in no sampled image "
                "header, so the reference is taken from the scanner's own Radiation "
                "Dose SR instead."
            ),
            # Narrowed on 2026-11-08: this used to say the arm tests "the dose model",
            # which a reader could quote as a validation while the note two keys below
            # says the reference and the coefficients share a source and only the
            # arithmetic is tested. A reviewer pointed out that the block contained both
            # readings. The wider claim is gone rather than qualified.
            "what_this_tests": (
                "the arithmetic that turns recorded metadata into a dose index on GE "
                "scanners: the model match, the phantom and tube-potential lookup -- at "
                "120 kVp only, which is the single potential all five events use, so the "
                "interpolation and refusal paths are not exercised here -- mAs per "
                "rotation from current x time, and the division by pitch. It does "
                "NOT test the choice of collimation row or bowtie filter -- those are "
                "varied over, which is what the reported range is -- so an error in "
                "either would widen the range rather than show up as disagreement. A "
                "reviewer pointed out on 2026-11-08 that listing them as tested "
                "overstated the arm. It does NOT test the metadata-gap recovery path, "
                "because the technique is read from a structured report rather than "
                "reconstructed from a de-identified image header; and it does NOT test "
                "the vendor's dosimetry, for the reason given under "
                "'the_reference_and_the_coefficients_share_a_source'."
            ),
            # Added 2026-11-08 after a reviewer asked why the one corroborated event
            # agrees to the last digit. It does, and the reason matters more than the
            # agreement: 22.26 mGy / (140 mAs / 100) = 15.900000000000002 against a
            # tabulated 15.9.
            "the_reference_and_the_coefficients_share_a_source": (
                "The reference here is the CTDIvol the scanner displayed and wrote into "
                "its own dose report, which is pre-set by the manufacturer from "
                "in-factory measurements. The open coefficient table is the MIRDct "
                "survey, whose values are console-reported CTDIvol normalized to 100 mAs "
                "(Dinwiddie et al. 2026, sections 2.1-2.2) -- also console figures, from "
                "the same factory calibration. The two are therefore NOT independent. "
                "What this arm tests is the arithmetic that turns recorded metadata into "
                "a dose index: the model match, the phantom and tube-potential lookup "
                "(at 120 kVp only here), mAs "
                "per rotation from current x time, and the pitch division. It does NOT "
                "test the vendor's dosimetry, and where an event's technique coincides "
                "with a tabulated row exact agreement is close to tautological -- which "
                "is what the one corroborated event shows. Nor does it test the choice "
                "of collimation row or bowtie filter: those are varied over, which is "
                "what the reported range is. See 'what_this_tests'."
            ),
            "independence": (
                "Events are not independent patients. The comparison draws several "
                "events from each study, and events within a study share a scanner, a "
                "calibration and a session."
            ),
            # Added on 2026-11-08, after a reviewer pointed out that the one event with
            # a sharp comparison has pitch 1.0 and so cannot tell dividing by pitch from
            # ignoring it. The sample does answer the question, but not through that
            # event: it answers through the agreement of the implied coefficient across
            # two pitches on the same model and phantom.
            "what_the_sample_can_discriminate": {
                "the_pitch_division": (
                    "Yes, though not through the exact event, whose pitch is 1.0. The "
                    "implied coefficient is nCTDIw = recorded CTDIvol * pitch / "
                    "(mAs_rot / 100), so omitting or inverting the pitch division would "
                    "separate two events of the same model and phantom by their pitch "
                    "ratio. The LightSpeed VCT at 32 cm gives 8.478 at pitch 0.97 and "
                    "8.504 at pitch 1.38, agreeing to 0.31 % where the ratio is 1.42; the "
                    "Optima 660 gives 8.657 at 0.98 and 8.740 at 1.38, agreeing to "
                    "0.95 %. That is the sharpest arithmetic check in this arm."
                ),
                "the_coefficient_lookup": (
                    "Weakly. A tabulated row matches the recorded value by construction "
                    "where the technique coincides with one, so the exact event does not "
                    "test which row was chosen. What the sample does show is that every "
                    "implied coefficient falls inside the range its own model and phantom "
                    "tabulate -- see cases[].implied_nctdi_w_mgy_per_100mas against the "
                    "nctdi_w_mgy_per_100mas values in the same case's assumption_envelope "
                    "grid. Hand-written figures stood here until a reviewer found them "
                    "wrong on 2026-11-08; the comparison is now left to the rows, which "
                    "a test re-derives."
                ),
                "the_collimation_and_filter_choice": (
                    "No. Those are varied over rather than tested, which is what the "
                    "reported range is; an error in either would widen the range rather "
                    "than show as disagreement."
                ),
                "an_error_of_a_few_per_cent": (
                    "No, on the four refuted events: their spans are about 34 percentage "
                    "points wide, so a 2-3 % error anywhere in the chain would sit inside "
                    "them undetected. Only the exact event, where the recorded geometry "
                    "stands, is sharp -- and its reference is a console figure from the "
                    "same calibration as the table, so it tests transcription more than "
                    "dosimetry."
                ),
            },
            "coefficients": provenance(),
            "sample_source": (
                "OpenREM test objects (GPL-3.0), openrem/remapp/tests/test_files/CT-*.dcm, "
                "and a PixelMed sample. The objects are third-party and are not "
                "redistributed here; this file holds only measurements read out of them."
            ),
            "sample_directory": str(sample_dir),
            # These are the rules exclusion_reason() applies, in the order it applies
            # them. They were wrong until 2026-11-08: rule 3 still said "table feed
            # (helical)" after the code had been corrected to admit sequenced
            # (step-and-shoot) acquisitions, and rule 4 did not exist at all although
            # four events are excluded by it. Two independent reviewers found the
            # mismatch. A published rule that is not the rule applied misstates how the
            # comparison set was defined, which is the one thing this block is for.
            "eligibility_rules": [
                "the event records a CTDIvol (DCM 113830)",
                "the scanner model resolves in the open coefficient database, exactly "
                "or by an alias denoting the same machine",
                "the acquisition type is one in which the table moves -- spiral, or "
                "sequenced, where the table advances between rotations (CID 10013) -- "
                "because the formula models one rotation's output spread along the feed. "
                "Stationary, constant-angle and free acquisitions are excluded: their "
                "recorded CTDIvol accumulates over the dwell, so exposure time per "
                "rotation does not describe the exposure behind it",
                f"the geometry is not degenerate. A recorded pitch at or below "
                f"{DEGENERATE_PITCH} cannot be carrying the table, so such an event is "
                f"excluded unless the report records a positive scanning length to show "
                f"that it moved -- an absent DCM 113825 does not rescue it, and nor does "
                f"a recorded 0. Four events record 0.100 over a scanning length of "
                f"0.0 mm. A low pitch over a recorded positive distance is a slow feed "
                f"and is admitted, which is why the pitch alone does not decide. A "
                f"recorded 0 at a pitch that would carry the table is contradictory and "
                f"is excluded under its own reason. Where the pitch is real and only the "
                f"scanning length is absent, the event is admitted: the CTDIvol formula "
                f"does not use the length, so only the beam-width cross-check is lost "
                f"and the case reports itself as not measured",
                "tube potential, mean tube current, exposure time per rotation, pitch, "
                "total collimation and the CTDIw phantom code are all recorded",
                "the technique falls inside the tabulated grid: the lookup must not "
                "refuse the tube potential as outside the measured range, and the "
                "assumption grid must hold at least one exactly tabulated cell -- which "
                "is what rejects an interpolated potential, since a cell is admitted only "
                "when the lookup records no assumption at all. The point estimate itself "
                "may rest on an interpolated potential, and records that on the case. "
                "This rule is applied after the others, when the lookup is attempted, and "
                "an event failing it is counted under 'technique outside the tabulated "
                "grid'. A reviewer noted on 2026-11-08 that it was being applied without "
                "being published, and on the next round that its first clause claimed "
                "more than the code does",
            ],
        },
        "formula_input_check": check_formula_inputs(),
        "summary": {
            "n_files": len(files),
            "n_events_seen": sum(f["n_events"] for f in files),
            "n_events_with_ctdivol": sum(f["n_events_with_ctdivol"] for f in files),
            "n_evaluated": len(cases),
            "n_excluded": len(excluded),
            "exclusions_by_reason": reasons,
            # Per model, the band -- not the point estimate. Until 2026-11-08 this key
            # was "by_model" and held describe() of the point errors, including
            # "within_20_percent": 1.0, with no label saying what it rested on. For the
            # Optima 660 those errors came from the tuple-order 'small' (head) bowtie at
            # the 10 mm row, which that event's own exposure time refutes -- the exact
            # defect the band was introduced to remove, still published beside the band
            # that removed it. A reviewer found it. The key now names what it reports,
            # and the point estimate appears once, under its own long name.
            "by_model_reported_band": {
                m: _band_over_models([c for c in cases if c["table_model"] == m])
                for m in sorted({c["table_model"] for c in cases})
            },
            "by_model_point_estimate_under_the_default_assumptions": {
                m: describe([c["percent_error"] for c in cases if c["table_model"] == m])
                for m in sorted({c["table_model"] for c in cases})
            },
            "n_exact_table_match": sum(1 for c in cases if c["exact_table_match"]),
            "pass_condition": (
                f"for each event, the admissible combination of bowtie filter and "
                f"collimation that comes closest to the recorded CTDIvol must come within "
                f"{100 * ARITHMETIC_TOLERANCE:g} % of it -- a round threshold, stated "
                f"above both the rounding of the two numbers compared (0.02 % to 0.15 % "
                f"on the recorded value, about 0.05 % on the coefficients) and the "
                f"observed best-cell errors (0.00 % to 0.50 %), and claimed to follow "
                f"from neither; every event's actual best-cell error is published so a "
                f"stricter threshold can be applied. An event "
                f"where no admissible combination came that close would be one the open "
                f"table cannot reproduce however those inputs are chosen, and would count "
                f"against the arithmetic. The condition is evaluated separately under "
                f"every reading the arm publishes, so that stating it does not choose "
                f"between them."
            ),
            # Per reading, and tolerant of a reading that has no admissible
            # combination for some event: such a reading carries no percent_error, and
            # reading one crashed run() until 2026-11-08. It counts as not within
            # tolerance and is counted separately, so a worst case is never reported over
            # a silently reduced subset.
            "arithmetic_reproduced_by_reading": {
                reading: _reading_aggregate(cases, reading)
                for reading in sorted(
                    {k for c in cases for k in c["arithmetic_reproduced"]}
                )
            },
            # Kept, and demoted. This is what one particular set of assumptions gives,
            # and reporting it alone was how an arbitrary tie-break between two equally
            # distant bowtie filters came to decide the headline. Read it beside
            # "headline" below, never instead of it.
            "point_estimate_under_the_default_assumptions": describe(errors),
            "headline": _headline(cases),
            # Split by phantom. A median or a standard deviation over both mixes two
            # different quantities -- the 16 cm and 32 cm coefficients differ by about a
            # factor of two by construction -- and the pooled figure was printed as one
            # range. A reviewer found it on 2026-11-08.
            "implied_coefficient_mgy_per_100mas_by_phantom": {
                f"{phantom}cm": describe_quantity(
                    [
                        c["implied_nctdi_w_mgy_per_100mas"]
                        for c in cases
                        if c["phantom_cm"] == phantom
                    ],
                    "mGy per 100 mAs at pitch 1",
                )
                for phantom in sorted({c["phantom_cm"] for c in cases})
            },
        },
        "files": files,
        "cases": cases,
        "excluded": excluded,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    repo = Path(__file__).resolve().parents[1]
    ap.add_argument(
        "samples",
        nargs="?",
        default=os.environ.get("CTDOSE_RDSR_SAMPLES", ""),
        help="directory of CT Radiation Dose SR objects",
    )
    ap.add_argument("--out", type=Path, default=repo / "results")
    args = ap.parse_args()

    if not args.samples or not Path(args.samples).is_dir():
        print("set CTDOSE_RDSR_SAMPLES or pass a directory of CT RDSR objects")
        return 2

    payload = run(Path(args.samples))
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "ge_rdsr_accuracy.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    s = payload["summary"]
    check = payload["formula_input_check"]
    print(f"GE files                      : {s['n_files']}")
    print(f"irradiation events            : {s['n_events_seen']}")
    print(f"events recording a CTDIvol    : {s['n_events_with_ctdivol']}")
    print(f"events compared               : {s['n_evaluated']}")
    print(f"formula ignores invented input: {check['invariant']}")
    print("\nexclusions:")
    for reason, n in sorted(s["exclusions_by_reason"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {reason}")
    # The band first, then the point estimate, and the point estimate labelled for what
    # it is. Printing "within20 100%" at the foot of this summary was how the withdrawn
    # single-number headline kept presenting itself as the result.
    print("\nper event -- the reported band, over the choices the report leaves open:")
    for c in payload["cases"]:
        band = c["reported_band"]
        print(
            f"  {c['file'][:22]:24} #{c['event_index']:<3} {c['table_model']:16} "
            f"{c['phantom_cm']:>2}cm  recorded {c['recorded_ctdivol_mgy']:7.2f} mGy  "
            f"{band['percent_error_min']:+7.1f}% to {band['percent_error_max']:+7.1f}%  "
            f"[{band['assumed']}]"
        )
        print(f"      {band.get('because', '')}")

    head = s["headline"]
    print("\nheadline:")
    for key, label in (
        ("geometry_corroborated_by_exposure_time", "geometry corroborated"),
        ("geometry_refuted_by_exposure_time", "geometry refuted   "),
    ):
        b = head.get(key) or {}
        if not b.get("n"):
            continue
        wide = b["over_every_tabulated_collimation_and_filter"]
        print(
            f"  {label}  n={b['n']}  "
            f"{b['worst_case_percent_error_min']:+.1f}% to "
            f"{b['worst_case_percent_error_max']:+.1f}%  [{b['assumed']}]"
        )
        print(
            f"      over every tabulated collimation and filter: "
            f"{wide['worst_case_percent_error_min']:+.1f}% to "
            f"{wide['worst_case_percent_error_max']:+.1f}%"
        )

    o = s["point_estimate_under_the_default_assumptions"]
    if o.get("n"):
        print(
            f"\npoint estimate at the nearest row to the RECORDED collimation -- not the "
            f"result, and\nrefuted by the exposure time on "
            f"{head['n_refuted']} of {o['n']} events: median {o['median']:+.1f}%  "
            f"range {o['min']:+.1f}..{o['max']:+.1f}%"
        )
    print("\ncoefficient implied by the recorded CTDIvol, by phantom:")
    for phantom, imp in s["implied_coefficient_mgy_per_100mas_by_phantom"].items():
        if not imp.get("n"):
            continue
        print(
            f"  {phantom:>5}  {imp['min']:.2f}..{imp['max']:.2f} mGy/100mAs "
            f"(median {imp['median']:.2f}) across {imp['n']} event(s)"
        )
    print("\ndoes the scanner's own exposure time agree with the collimation it recorded?")
    for c in payload["cases"]:
        b = c["beam_width_consistency"]
        if not b.get("measured"):
            print(f"  {c['file'][:22]:24} #{c['event_index']:<3} not measurable")
            continue
        print(
            f"  {c['file'][:22]:24} #{c['event_index']:<3} "
            f"recorded {b['recorded_collimation_mm']:5.1f} mm   "
            f"exposure {b['exposure_time_total_s']:6.2f} s implies "
            f"{b['implied_collimation_mm']:5.1f} mm   "
            f"{'CORROBORATED' if b['corroborated'] else 'REFUTED by its own clock'}"
        )

    print("\nthe range the unrecorded choices allow, per event:")
    for c in payload["cases"]:
        e = c["assumption_envelope"]
        if not e.get("grid"):
            continue
        print(
            f"  {c['file'][:22]:24} #{c['event_index']:<3} "
            f"{e['percent_error_min']:+7.1f}% .. {e['percent_error_max']:+7.1f}%  "
            f"over {e['n_combinations']} combination(s) of "
            f"{len(e['bowties_tabulated'])} bowtie x "
            f"{len(e['collimations_tabulated'])} collimation"
            + ("" if e["conventional_bowtie_is_tabulated"]
               else f"   [conventional {e['conventional_bowtie_for_phantom']!r} filter not tabulated]")
        )

    corroborated = [
        c for c in payload["cases"] if c["beam_width_consistency"].get("corroborated")
    ]
    print(
        f"\ngeometry corroborated by the exposure time: {len(corroborated)} of "
        f"{len(payload['cases'])} event(s)"
    )
    for c in corroborated:
        print(
            f"  {c['file'][:22]:24} #{c['event_index']:<3} "
            f"error {c['percent_error']:+.1f}% with no collimation substitution"
        )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
