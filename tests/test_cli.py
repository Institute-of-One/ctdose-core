"""CLI smoke tests."""

from __future__ import annotations

import json

import pytest

from ctdose_core.cli import main


def test_models_lists_the_table(capsys):
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert "GE LightSpeed VCT (small BS)" in out
    assert len(out.strip().splitlines()) == 30


def test_models_regions(capsys):
    assert main(["models", "--regions"]) == 0
    out = capsys.readouterr().out
    assert "Chest" in out and "Whole abdomen" in out


def test_drl_listing_includes_the_documented_note(capsys):
    assert main(["drl"]) == 0
    out = capsys.readouterr().out
    assert "Routine chest" in out
    assert "Japan DRLs 2015" in out
    assert "note:" in out  # the corrected paediatric cell explains itself


@pytest.mark.fixture_data
def test_inspect_reports_the_missing_dose_tags(dose_taro_images, capsys):
    assert main(["inspect", str(dose_taro_images)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model_name"] == "LightSpeed VCT"
    assert payload["ctdivol_tag"]["value"] is None
    assert payload["pitch"]["value"] == pytest.approx(0.984)


@pytest.mark.fixture_data
def test_analyze_human_readable(dose_taro_images, capsys):
    code = main(
        [
            "analyze",
            str(dose_taro_images),
            "--region",
            "Chest",
            "--scanner-model",
            "GE LightSpeed VCT (small BS)",
            "--drl-key",
            "routine_chest",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "CTDIvol" in out and "physics_fallback" in out
    assert "SSDE" in out
    assert "DRL CTDIvol" in out


@pytest.mark.fixture_data
def test_analyze_json_round_trips(dose_taro_images, tmp_path, capsys):
    out_path = tmp_path / "report.json"
    assert (
        main(
            [
                "analyze",
                str(dose_taro_images),
                "--region",
                "Chest",
                "--scanner-model",
                "GE LightSpeed VCT (small BS)",
                "--no-size",
                "--out",
                str(out_path),
            ]
        )
        == 0
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["quantities"]["ctdivol"]["source"] == "physics_fallback"
    assert payload["region"] == "Chest"
    assert payload["series"]["n_slices"] > 0


@pytest.mark.fixture_data
def test_survey_reports_tag_coverage(dose_taro_images, capsys):
    assert main(["survey", str(dose_taro_images.parent)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["coverage"]["n_series"] >= 1
    assert payload["coverage"]["fraction_needing_fallback"] == 1.0
    assert "TCIA Data Usage Policy" in payload["coverage"]["note"]
