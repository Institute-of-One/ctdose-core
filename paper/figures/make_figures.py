"""Generate every manuscript figure from ``results/*.json`` and the bundled fixture.

No figure may contain a number that is not in ``results/``; every panel here is drawn
straight from those files, so a re-run of the tools followed by a re-run of this script
keeps the figures and the text in step. ``tests/test_manuscript_consistency.py`` checks
that the manuscript quotes the same values.

Usage::

    python paper/figures/make_figures.py                       # results-derived figures
    python paper/figures/make_figures.py --series <dir>        # + the modulation figure
    python paper/figures/make_figures.py --series <dir> --masks <dir>   # + organ overlay

Outputs 300-dpi PNGs into ``paper/figures/`` plus ``figure_data.json`` recording the
exact values plotted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "results"
FIGDIR = Path(__file__).resolve().parent
DPI = 300

#: Consistent vendor ordering and colours across every figure.
VENDORS = ["GE", "Siemens", "Philips", "Canon/Toshiba"]
VENDOR_COLOUR = {
    "GE": "#4c72b0",
    "Siemens": "#dd8452",
    "Philips": "#55a868",
    "Canon/Toshiba": "#c44e52",
}

plt.rcParams.update(
    {
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 120,
    }
)


def _load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def stable_jitter(key: str, spread: float = 0.22) -> float:
    """Deterministic offset in ``[-spread, +spread]`` derived from a string.

    Python randomises ``hash()`` for strings per process unless PYTHONHASHSEED is set, so
    seeding a generator from it would make the figure differ on every run. A cryptographic
    digest does not have that problem, and the figure is then byte-reproducible.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    unit = int.from_bytes(digest[:8], "big") / float(1 << 64)  # [0, 1)
    return (2.0 * unit - 1.0) * spread


def _save(fig, name: str) -> Path:
    out = FIGDIR / name
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out.relative_to(REPO)}")
    return out


# --- Figure: Layer-1 vendor-neutral RDSR agreement ---------------------------------------


def figure_layer1(layer1: dict) -> dict[str, Any]:
    """Recorded CTDIvol/DLP read from real vendor RDSR objects, and the two consistency
    checks that validate the read."""
    files = [f for f in layer1["files"] if f.get("is_ct_rdsr")]
    events = layer1["events"]

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(9.0, 3.8))

    # Panel A: per-event CTDIvol by vendor, split by reference phantom.
    xs, ys, colours, markers = [], [], [], []
    for e in events:
        if e["ctdivol_mgy"] is None:
            continue
        xs.append(VENDORS.index(e["vendor"]) + stable_jitter(f"{e['file']}#{e['event_index']}"))
        ys.append(e["ctdivol_mgy"])
        colours.append(VENDOR_COLOUR[e["vendor"]])
        markers.append(e["phantom_cm"])
    for phantom, marker in ((16, "^"), (32, "o")):
        sel = [i for i, m in enumerate(markers) if m == phantom]
        if sel:
            ax0.scatter(
                [xs[i] for i in sel], [ys[i] for i in sel],
                c=[colours[i] for i in sel], marker=marker, s=28,
                edgecolors="white", linewidths=0.5, zorder=3,
            )
    # Neutral markers in the legend: the shapes encode phantom, the colours encode vendor,
    # and a coloured legend swatch here would read as a vendor key.
    ax0.legend(
        handles=[
            plt.Line2D([], [], marker="^", ls="", color="0.35", markersize=6,
                       label="16 cm head phantom"),
            plt.Line2D([], [], marker="o", ls="", color="0.35", markersize=6,
                       label="32 cm body phantom"),
        ],
        frameon=False, loc="upper left",
    )
    ax0.set_yscale("log")
    ax0.set_xticks(range(len(VENDORS)))
    ax0.set_xticklabels(VENDORS, rotation=12)
    ax0.set_ylabel("Recorded CTDI$_{vol}$ (mGy)")
    ax0.set_title("(A) Per-event CTDI$_{vol}$ read from vendor RDSR")
    ax0.grid(axis="y", alpha=0.25, which="both")

    # Panel B: sum of per-event DLP against the SR's own accumulated total.
    pairs = [
        (f["summed_event_dlp_mgycm"], f["recorded_total_dlp_mgycm"], f["vendor"])
        for f in files
        if f["dlp_sum_vs_total"].get("comparable")
    ]
    lim_lo = min(min(p[0], p[1]) for p in pairs) * 0.6
    lim_hi = max(max(p[0], p[1]) for p in pairs) * 1.6
    ax1.plot([lim_lo, lim_hi], [lim_lo, lim_hi], color="0.6", lw=1, ls="--", zorder=1)
    for vendor in VENDORS:
        sel = [(a, b) for a, b, v in pairs if v == vendor]
        if sel:
            ax1.scatter(
                [a for a, _ in sel], [b for _, b in sel],
                c=VENDOR_COLOUR[vendor], s=34, edgecolors="white", linewidths=0.5,
                label=vendor, zorder=3,
            )
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlim(lim_lo, lim_hi)
    ax1.set_ylim(lim_lo, lim_hi)
    ax1.set_xlabel(r"$\Sigma$ per-event DLP (mGy$\cdot$cm)")
    ax1.set_ylabel(r"Accumulated DLP recorded in the SR (mGy$\cdot$cm)")
    check = layer1["summary"]["dlp_sum_check"]
    ax1.set_title(
        f"(B) Internal consistency: {check['files_within_tolerance']}/"
        f"{check['files_comparable']} within {check['tolerance']:.0%}"
    )
    ax1.legend(frameon=False, loc="upper left")
    ax1.grid(alpha=0.25, which="both")

    fig.tight_layout()
    _save(fig, "fig1_layer1_rdsr_agreement.png")

    return {
        "n_events_plotted": len(ys),
        "ctdivol_min": min(ys),
        "ctdivol_max": max(ys),
        "dlp_pairs": len(pairs),
        "dlp_within_tolerance": check["files_within_tolerance"],
        "dlp_comparable": check["files_comparable"],
    }


# --- Figure: Layer-2 recorded-dose availability -------------------------------------------


def figure_availability(survey: dict) -> dict[str, Any]:
    """Per-vendor CTDIvol absence, fallback feasibility and overall surveyability."""
    absence = survey["tag_absence"]
    by_vendor = absence["by_vendor"]

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(10.2, 4.3), gridspec_kw={"width_ratios": [1.1, 1]})

    x = np.arange(len(VENDORS))
    width = 0.27
    absent = [100 * by_vendor[v]["ctdivol_absence_rate"] for v in VENDORS]
    feasible = [100 * by_vendor[v]["fallback_feasibility_rate"] for v in VENDORS]
    surveyable = [100 * by_vendor[v]["surveyable_rate"] for v in VENDORS]

    # Sequential greys-to-teal, deliberately unrelated to the vendor palette: the bars
    # encode a metric, and the vendor is already on the x-axis.
    ax0.bar(x - width, absent, width, label="CTDI$_{vol}$ absent from header", color="#4a4a4a")
    ax0.bar(x, feasible, width, label="physics reconstruction feasible", color="#7fa8b5")
    ax0.bar(x + width, surveyable, width, label="dose recoverable by either route", color="#bcd7c9")
    for xi, v in zip(x, VENDORS, strict=True):
        n = by_vendor[v]["n_series_readable"]
        ax0.annotate(f"n={n}", xy=(xi, 102), ha="center", fontsize=7.5, color="0.35")
    ax0.set_xticks(x)
    ax0.set_xticklabels(VENDORS, rotation=12)
    ax0.set_ylabel("percentage of screened series (%)")
    ax0.set_ylim(0, 128)
    ax0.set_title("(A) Recorded-dose availability by manufacturer")
    ax0.legend(frameon=False, loc="upper left", ncol=1, fontsize=7.5)
    ax0.grid(axis="y", alpha=0.25)

    # Panel B: which acquisition attributes are present, per vendor.
    attrs = [
        ("has_tube_current", "tube current"),
        ("has_timing", "rotation or\nexposure time"),
        ("has_collimation", "total\ncollimation"),
        ("has_pitch", "pitch"),
        ("has_slice_thickness", "slice\nthickness"),
        ("has_slice_location", "slice\nlocation"),
    ]
    matrix = np.array(
        [[100 * by_vendor[v]["attribute_presence"][k] for k, _ in attrs] for v in VENDORS]
    )
    im = ax1.imshow(matrix, cmap="YlGnBu", vmin=0, vmax=100, aspect="auto")
    ax1.set_xticks(range(len(attrs)))
    ax1.set_xticklabels([lbl for _, lbl in attrs], fontsize=7.5, rotation=30, ha="right")
    ax1.set_yticks(range(len(VENDORS)))
    ax1.set_yticklabels(VENDORS)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax1.text(
                j, i, f"{matrix[i, j]:.0f}", ha="center", va="center", fontsize=7.5,
                color="white" if matrix[i, j] > 55 else "0.2",
            )
    ax1.set_title("(B) Acquisition attributes present (%)")
    fig.colorbar(im, ax=ax1, fraction=0.035, pad=0.03).set_label("% of series", fontsize=8)

    fig.tight_layout()
    _save(fig, "fig2_recorded_dose_availability.png")

    return {
        "absent_pct": dict(zip(VENDORS, [round(a, 1) for a in absent], strict=True)),
        "feasible_pct": dict(zip(VENDORS, [round(a, 1) for a in feasible], strict=True)),
        "surveyable_pct": dict(zip(VENDORS, [round(a, 1) for a in surveyable], strict=True)),
        "n_per_vendor": {v: by_vendor[v]["n_series_readable"] for v in VENDORS},
    }


# --- Figure: Layer-2 fallback error --------------------------------------------------------


def figure_fallback_error(survey: dict) -> dict[str, Any]:
    """The reconstruction's error, and why it must be read stratified."""
    cases = survey["fallback_cases"]
    fb = survey["fallback_error"]
    threshold = 0.5  # the diagnostic-acquisition floor used throughout
    headline = fb["diagnostic_and_collimation_measured"]

    complete = [
        c for c in cases
        if c["collimation_measured"] and c["recorded_ctdivol_mgy"] >= threshold
    ]
    other = [c for c in cases if c not in complete]

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(9.4, 3.9), gridspec_kw={"width_ratios": [1.1, 1]})

    # Panel A: recorded vs reconstructed CTDIvol, identity line, split by stratum.
    lim = (0.03, 200)
    ax0.plot(lim, lim, color="0.6", lw=1, ls="--", zorder=1, label="identity")
    for factor, style in ((2.0, ":"), (0.5, ":")):
        ax0.plot(lim, [factor * v for v in lim], color="0.8", lw=0.8, ls=style, zorder=1)
    present = [v for v in VENDORS if any(c["vendor"] == v for c in cases)]
    for group, marker, alpha in ((complete, "o", 0.95), (other, "x", 0.75)):
        for vendor in present:
            sel = [c for c in group if c["vendor"] == vendor]
            if not sel:
                continue
            ax0.scatter(
                [c["recorded_ctdivol_mgy"] for c in sel],
                [c["estimated_ctdivol_mgy"] for c in sel],
                marker=marker, s=34, alpha=alpha, c=VENDOR_COLOUR[vendor],
                edgecolors="white" if marker == "o" else None,
                linewidths=0.5, zorder=3, label="_nolegend_",
            )
    handles = [
        plt.Line2D([], [], marker="o", ls="", color=VENDOR_COLOUR[v], label=v, markersize=6)
        for v in present
    ] + [
        plt.Line2D([], [], marker="o", ls="", color="0.4", markersize=6,
                   label="diagnostic +\ncollimation recorded"),
        plt.Line2D([], [], marker="x", ls="", color="0.4", markersize=6,
                   label="excluded from\nheadline stratum"),
    ]
    ax0.set_xscale("log")
    ax0.set_yscale("log")
    ax0.set_xlim(*lim)
    ax0.set_ylim(*lim)
    ax0.set_xlabel("recorded CTDI$_{vol}$ (mGy)")
    ax0.set_ylabel("reconstructed CTDI$_{vol}$ (mGy)")
    ax0.set_title(f"(A) Reconstruction vs recorded value (n={len(cases)})")
    ax0.legend(handles=handles, frameon=False, loc="upper left", fontsize=7, ncol=1)
    ax0.grid(alpha=0.25, which="both")

    # Panel B: error distribution by stratum.
    # A single-hue ramp, deliberately not the vendor palette: these rows are strata, and
    # reusing the vendor colours here would read as a vendor breakdown.
    strata = [
        ("all cases", [c["percent_error"] for c in cases], "#cfcfcf"),
        (
            "collimation\nreconstructed",
            [c["percent_error"] for c in cases if not c["collimation_measured"]],
            "#9c9c9c",
        ),
        (
            "collimation\nrecorded",
            [c["percent_error"] for c in cases if c["collimation_measured"]],
            "#5f5f5f",
        ),
        ("diagnostic +\ncollimation recorded", [c["percent_error"] for c in complete], "#1a3d5c"),
    ]
    positions = np.arange(len(strata))
    for pos, (label, values, colour) in zip(positions, strata, strict=True):
        v = np.asarray(values, dtype=float)
        v = np.clip(v, -100, 150)
        offsets = np.array([stable_jitter(f"{label}#{i}", 0.13) for i in range(v.size)])
        ax1.scatter(
            v, np.full(v.size, pos) + offsets,
            s=22, color=colour, alpha=0.75, edgecolors="white", linewidths=0.4, zorder=3,
        )
        med = float(np.median(values))
        ax1.plot([med, med], [pos - 0.28, pos + 0.28], color="0.15", lw=2, zorder=4)
        ax1.annotate(
            f"n={len(values)}  median {med:+.1f}%",
            xy=(155, pos), fontsize=7.5, va="center", ha="right", color="0.25",
        )
    ax1.axvline(0, color="0.6", lw=1, ls="--")
    ax1.axvspan(-20, 20, color="0.9", zorder=0)
    ax1.set_yticks(positions)
    ax1.set_yticklabels([s[0] for s in strata])
    ax1.set_xlim(-110, 160)
    ax1.set_xlabel("relative error of the reconstruction (%)")
    ax1.set_title("(B) Error distribution by stratum")
    ax1.grid(axis="x", alpha=0.25)
    ax1.set_ylim(-0.6, len(strata) - 0.25)
    ax1.annotate(
        "shaded band: ±20%",
        xy=(0.5, -0.03), xycoords="axes fraction", fontsize=7, color="0.4", ha="center",
    )

    fig.tight_layout()
    _save(fig, "fig3_fallback_error.png")

    return {
        "n_cases": len(cases),
        "n_headline": len(complete),
        "headline_median": round(float(np.median([c["percent_error"] for c in complete])), 1),
        "reported_headline_median": headline["median"],
        "reported_headline_n": headline["n"],
    }


# --- Optional image figures ------------------------------------------------------------------


def figure_modulation(series_dir: Path, case: dict[str, Any] | None = None) -> dict[str, Any]:
    from ctdose_core.figures import save_modulation_figure
    from ctdose_core.io import load_series

    series = load_series(series_dir)
    # Title the figure by the public subject, not by the local folder: a reader has to be
    # able to fetch the same images, and the subject ID is what identifies them in TCIA.
    subject = f"{case['collection']} {case['subject_id']} - " if case else ""
    out = save_modulation_figure(
        series,
        FIGDIR / "fig4_modulation_and_wed.png",
        title=(
            f"{subject}{series.manufacturer} {series.model_name} "
            f"({series.n_slices} slices, {series.kvp:.0f} kV)"
        ),
        dpi=DPI,
    )
    print(f"  wrote {out.relative_to(REPO)}")
    return {
        **_case_identity(case),
        "series": f"{series.manufacturer} {series.model_name}",
        "protocol": series.protocol_name,
        "n_slices": series.n_slices,
        "kvp": series.kvp,
        "mean_tube_current_ma": round(series.mean_tube_current_ma, 1),
        "min_tube_current_ma": float(np.min(series.tube_current_ma)),
        "max_tube_current_ma": float(np.max(series.tube_current_ma)),
        "scan_range_mm": series.scan_range_mm,
    }


def _case_identity(case: dict[str, Any] | None) -> dict[str, Any]:
    """The public identity of the demonstration case, carried into ``figure_data.json``.

    Figures made from images a reader cannot obtain are not reproducible, so every image
    figure records what to download rather than where it happened to live on one machine.
    """
    if not case:
        return {
            "public_case": False,
            "note": "generated from a local series with no public identifier",
        }
    return {
        "public_case": True,
        "collection": case["collection"],
        "subject_id": case["subject_id"],
        "modality": case["modality"],
        "series_description": case["series_description"],
        "series_instance_uid": case["series_instance_uid"],
        "acquisition": case["acquisition"],
        "scanner": case["scanner"],
    }


def figure_organ_overlay(
    series_dir: Path, mask_dir: Path, case: dict[str, Any] | None = None
) -> dict[str, Any]:
    from ctdose_core.figures import organ_overlay_figure
    from ctdose_core.io import load_series
    from ctdose_core.organ import load_masks_from_dir, organ_weights_from_masks

    series = load_series(series_dir)
    masks = load_masks_from_dir(mask_dir, flip_ap=True)
    if not masks:
        raise SystemExit(f"no .nii.gz masks in {mask_dir}")

    subject = f"{case['collection']} {case['subject_id']} - " if case else ""
    fig = organ_overlay_figure(series, masks=masks, title=f"{subject}TotalSegmentator organs")
    _save(fig, "fig5_organ_overlay.png")

    profile = organ_weights_from_masks(masks, series.tube_current_ma)
    weights = {w.organ: round(w.relative_weight, 3) for w in profile.weights}
    ma = np.asarray(series.tube_current_ma, dtype=float)

    # Companion panel: the tube-current-weighted exposure index per organ. Every value
    # lies close to 1, so a dot plot on a zoomed axis is read far more easily than bars
    # from zero -- and, unlike a truncated bar chart, it does not exaggerate the spread.
    ordered = profile.weights[::-1]
    names = [w.organ.replace("_", " ") for w in ordered]
    values = [w.relative_weight for w in ordered]
    fig2, ax = plt.subplots(figsize=(6.6, 4.6))
    y = np.arange(len(names))
    ax.hlines(y, 1.0, values, color="#9db8c9", lw=2.0, zorder=2)
    ax.scatter(values, y, s=42, color="#1a3d5c", zorder=3, edgecolors="white", linewidths=0.6)
    ax.axvline(1.0, color="0.35", lw=1.2, ls="--", zorder=1)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    span = max(abs(min(values) - 1.0), abs(max(values) - 1.0))
    ax.set_xlim(1 - 1.45 * span, 1 + 1.45 * span)
    ax.set_ylim(-0.8, len(names) - 0.2)
    ax.set_xlabel("tube-current-weighted exposure relative to the scan mean, $w_o$")
    ax.set_title("Relative organ exposure index")
    ax.annotate(
        "dashed line: $w_o = 1$, the organ sees the scan-mean tube current",
        xy=(0.5, -0.155), xycoords="axes fraction", ha="center", fontsize=7.5, color="0.35",
    )
    ax.grid(axis="x", alpha=0.25)
    fig2.tight_layout()
    _save(fig2, "fig6_organ_exposure_index.png")

    return {
        **_case_identity(case),
        "n_organs": len(masks),
        "relative_weights": weights,
        "w_min": round(min(weights.values()), 3),
        "w_max": round(max(weights.values()), 3),
        # The dynamic range of w_o is bounded by the modulation the scanner actually
        # applied; recording it stops the figure being read as a claim about how much
        # organ dose can differ in general.
        "tube_current_min_ma": float(ma.min()),
        "tube_current_max_ma": float(ma.max()),
        "tube_current_range_pct_of_mean": round(
            100.0 * (ma.max() - ma.min()) / float(ma.mean()), 1
        ),
    }


# --- main --------------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cases",
        type=Path,
        default=REPO / "paper" / "demonstration_cases.json",
        help="public demonstration cases; supplies the series and mask paths and the "
        "subject identity recorded with the figures",
    )
    ap.add_argument("--series", type=Path, help="override the case file's series directory")
    ap.add_argument("--masks", type=Path, help="override the case file's mask directory")
    args = ap.parse_args()

    case: dict[str, Any] | None = None
    series_dir, mask_dir = args.series, args.masks
    if args.cases.is_file():
        spec = json.loads(args.cases.read_text(encoding="utf-8"))
        case = next((c for c in spec["cases"] if "figures" in c.get("role", "")), None)
        if case is not None:
            series_dir = series_dir or Path(case["local_path"])
            mask_dir = mask_dir or (Path(case["mask_path"]) if case.get("mask_path") else None)
    if series_dir is not None and not Path(series_dir).is_dir():
        print(f"  (case images not present at {series_dir}; see docs/REPRODUCING_FIGURES.md)")
        series_dir = None

    FIGDIR.mkdir(parents=True, exist_ok=True)
    layer1 = _load("rdsr_layer1.json")
    survey = _load("survey.json")

    print("generating figures:")
    data: dict[str, Any] = {
        "_provenance": {
            "generated_by": "paper/figures/make_figures.py",
            "inputs": ["results/rdsr_layer1.json", "results/survey.json"],
            "dpi": DPI,
        },
        "fig1_layer1_rdsr_agreement": figure_layer1(layer1),
        "fig2_recorded_dose_availability": figure_availability(survey),
        "fig3_fallback_error": figure_fallback_error(survey),
    }
    if series_dir:
        data["fig4_modulation_and_wed"] = figure_modulation(series_dir, case)
        if mask_dir:
            data["fig5_organ_overlay"] = figure_organ_overlay(series_dir, mask_dir, case)
    else:
        print("  (skipping the image figures; pass --series to generate them)")

    (FIGDIR / "figure_data.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  wrote {(FIGDIR / 'figure_data.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
