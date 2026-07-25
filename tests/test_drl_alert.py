"""DRL comparison and the auditability contract for alerts."""

from __future__ import annotations

import pytest

from ctdose_core.alert import (
    Severity,
    drl_alerts,
    outlier_alerts,
    provenance_alerts,
    size_band,
    summarise,
)
from ctdose_core.drl import DISCLAIMER, compare, entries, lookup
from ctdose_core.provenance import Quantity, Source


def test_adult_lookup_and_pediatric_bands():
    adult = lookup("routine_chest", age_years=45)
    assert adult.ctdivol_mgy == 15.0
    assert adult.dlp_mgycm == 550.0
    assert adult.age_band is None

    infant = lookup("chest", age_years=0.5)
    toddler = lookup("chest", age_years=3.0)
    school = lookup("chest", age_years=8.0)
    assert [infant.age_band, toddler.age_band, school.age_band] == [
        "<1 year",
        "1-5 years",
        "6-10 years",
    ]
    assert infant.ctdivol_mgy < toddler.ctdivol_mgy < school.ctdivol_mgy


def test_adult_key_does_not_resolve_for_a_child():
    with pytest.raises(LookupError):
        lookup("routine_chest", age_years=5)


def test_unknown_key_lists_the_available_ones():
    with pytest.raises(LookupError, match="available keys"):
        lookup("nonexistent_exam")


def test_the_corrected_pediatric_cell_carries_its_note():
    entry = lookup("chest", age_years=3.0)
    assert entry.ctdivol_mgy == 14.0
    assert any("141" in n for n in entry.notes)


def test_comparison_reports_ratio_excess_and_the_survey_caveat():
    entry = lookup("routine_chest", age_years=50)
    results = compare(entry, ctdivol_mgy=22.5, dlp_mgycm=500.0, ctdivol_source="physics_fallback")
    by_metric = {r.metric: r for r in results}

    ctdi = by_metric["CTDIvol"]
    assert ctdi.exceeds
    assert ctdi.ratio == pytest.approx(1.5)
    assert ctdi.excess == pytest.approx(7.5)
    assert ctdi.percent_of_drl == pytest.approx(150.0)
    assert ctdi.measured_source == "physics_fallback"
    assert DISCLAIMER in ctdi.notes

    assert not by_metric["DLP"].exceeds


def test_alerts_state_metric_reference_excess_and_size_band():
    entry = lookup("routine_chest", age_years=50)
    comparisons = compare(entry, ctdivol_mgy=22.5, ctdivol_source="dicom_tag")
    alerts = drl_alerts(comparisons, diameter_cm=29.0)
    assert len(alerts) == 1
    a = alerts[0]

    assert a.severity is Severity.WARNING  # ratio >= 1.5
    assert a.metric == "CTDIvol"
    assert a.measured == 22.5
    assert a.reference == 15.0
    assert a.excess == pytest.approx(7.5)
    assert a.ratio == pytest.approx(1.5)
    assert a.size_band == "average (26-32 cm)"
    assert a.measured_source == "dicom_tag"
    assert a.evidence["drl_set"] == "Japan DRLs 2015"
    assert a.evidence["examination"] == "Routine chest"
    # Everything needed to re-derive the verdict is in the message itself.
    for token in ("22.5", "15", "Routine chest", "150% of DRL", "average"):
        assert token in a.message


def test_marginal_exceedance_is_a_notice_not_a_warning():
    entry = lookup("routine_chest", age_years=50)
    alerts = drl_alerts(compare(entry, ctdivol_mgy=16.0), diameter_cm=30.0)
    assert alerts[0].severity is Severity.NOTICE


def test_within_drl_is_silent_unless_a_full_audit_trail_is_requested():
    entry = lookup("routine_chest", age_years=50)
    comparisons = compare(entry, ctdivol_mgy=8.0, dlp_mgycm=300.0)
    assert drl_alerts(comparisons, diameter_cm=30.0) == []
    verbose = drl_alerts(comparisons, diameter_cm=30.0, include_within_drl=True)
    assert len(verbose) == 2
    assert all(a.severity is Severity.INFO for a in verbose)


@pytest.mark.parametrize(
    "diameter,expected",
    [
        (None, "unknown"),
        (18.0, "small (D_w < 20 cm)"),
        (24.0, "below-average (20-26 cm)"),
        (29.0, "average (26-32 cm)"),
        (35.0, "above-average (32-38 cm)"),
        (42.0, "large (D_w >= 38 cm)"),
    ],
)
def test_size_bands(diameter, expected):
    assert size_band(diameter) == expected


def test_reconstructed_values_are_flagged_with_their_formula():
    quantities = {
        "ctdivol": Quantity(
            7.5, "mGy", Source.PHYSICS_FALLBACK, formula="CTDIvol = nCTDIw * mAs_rot * k_OB / pitch"
        ),
        "dlp": Quantity(500.0, "mGy*cm", Source.DICOM_TEXT),
    }
    alerts = provenance_alerts(quantities)
    assert len(alerts) == 1
    assert alerts[0].code == "provenance.ctdivol"
    assert alerts[0].measured_source == "physics_fallback"
    assert "nCTDIw" in alerts[0].evidence["formula"]


def test_a_derived_value_resting_on_a_fallback_is_flagged_too():
    """DLP computed from a reconstructed CTDIvol inherits the caveat."""
    quantities = {
        "ctdivol": Quantity(7.5, "mGy", Source.DICOM_TAG),
        "dlp": Quantity(
            500.0, "mGy*cm", Source.DERIVED, inputs={"CTDIvol_source": "physics_fallback"}
        ),
    }
    codes = {a.code for a in provenance_alerts(quantities)}
    assert codes == {"provenance.dlp"}


def test_quantities_that_are_derived_by_definition_are_not_flagged():
    """The alert is about resting on a reconstruction, not about being computed.

    SSDE and effective dose are derived by definition; saying so adds no information.
    They are only flagged when the CTDIvol or DLP underneath them was reconstructed.
    """
    recorded = {
        "ctdivol": Quantity(7.5, "mGy", Source.DICOM_TAG),
        "dlp": Quantity(500.0, "mGy*cm", Source.DICOM_TEXT),
        "ssde": Quantity(11.0, "mGy", Source.DERIVED),
        "effective_dose": Quantity(6.0, "mSv", Source.DERIVED),
    }
    assert provenance_alerts(recorded) == []
    assert provenance_alerts(recorded, names=tuple(recorded)) == []

    on_a_fallback = dict(recorded)
    on_a_fallback["ssde"] = Quantity(
        11.0, "mGy", Source.DERIVED, inputs={"CTDIvol_source": "physics_fallback"}
    )
    wide = provenance_alerts(on_a_fallback, names=tuple(on_a_fallback))
    assert {a.code for a in wide} == {"provenance.ssde"}


def test_outlier_alert_shows_its_statistics():
    cohort = [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3]
    assert outlier_alerts(10.2, cohort) == []
    alerts = outlier_alerts(30.0, cohort)
    assert len(alerts) == 1
    ev = alerts[0].evidence
    assert ev["cohort_n"] == len(cohort)
    assert "median/MAD" in ev["method"]
    assert abs(ev["score"]) > 3.0
    assert alerts[0].severity is Severity.NOTICE


def test_outlier_needs_enough_cohort_to_be_meaningful():
    assert outlier_alerts(30.0, [10.0, 10.5, 9.8]) == []


def test_summary_reports_the_highest_severity():
    entry = lookup("routine_chest", age_years=50)
    alerts = drl_alerts(compare(entry, ctdivol_mgy=30.0), diameter_cm=30.0)
    alerts += provenance_alerts({"dlp": Quantity(500.0, "mGy*cm", Source.PHYSICS_FALLBACK)})
    s = summarise(alerts)
    assert s["highest_severity"] == "warning"
    assert s["n"] == 2
    assert s["counts"]["warning"] == 1


def test_every_shipped_drl_entry_is_well_formed():
    for e in entries():
        assert e.ctdivol_mgy > 0 and e.dlp_mgycm > 0
        assert e.phantom_cm in (16, 32)
        assert e.set_name
