"""Publication-quality figures: body contour, tube-current modulation and WED.

Reproduces, in matplotlib, the visual summary a reader expects from a CT dose
study: an axial slice with the automatically extracted body contour, and a
coronal ray-sum ("scout") with the per-slice tube-current (mA) modulation and the
per-slice water-equivalent diameter (WED) overlaid and annotated.

matplotlib is imported lazily; it is an optional ``[gui]`` extra, so importing
:mod:`ctdose_core` does not require it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .io import ScanSeries
from .segmentation import body_mask, central_slice_index, contour_metrics
from .ssde import water_equivalent_diameter


@dataclass
class SliceProfiles:
    """Per-slice quantities along the scan (z) axis."""

    z_index: np.ndarray
    tube_current_ma: np.ndarray
    wed_cm: np.ndarray
    central_index: int
    central_slice: np.ndarray
    central_mask: np.ndarray
    coronal_raysum: np.ndarray  # (nx, nz): z is the horizontal axis


def compute_profiles(series: ScanSeries, *, wed_step: int = 1) -> SliceProfiles:
    """Contour every ``wed_step``-th slice and build the modulation/WED profiles."""
    volume = series.load_volume()  # (nz, ny, nx) in HU
    nz = volume.shape[0]
    ps = series.pixel_spacing_mm or (1.0, 1.0)

    wed = np.full(nz, np.nan)
    for i in range(0, nz, wed_step):
        c = contour_metrics(volume[i], ps)
        if not c.is_empty:
            wed[i] = float(water_equivalent_diameter(c.mean_hu, c.area_cm2))
    if wed_step > 1:  # fill the skipped slices
        idx = np.arange(nz)
        good = ~np.isnan(wed)
        if good.sum() >= 2:
            wed = np.interp(idx, idx[good], wed[good])

    ma = np.asarray(series.tube_current_ma, dtype=float)
    if ma.size != nz:  # align lengths defensively
        z = np.linspace(0, 1, nz)
        ma = np.interp(z, np.linspace(0, 1, max(ma.size, 1)), ma) if ma.size else np.full(nz, np.nan)

    centre = central_slice_index(volume)
    cmask = body_mask(volume[centre], ps)
    coronal = volume.mean(axis=1).T  # (nx, nz)

    return SliceProfiles(
        z_index=np.arange(nz), tube_current_ma=ma, wed_cm=wed,
        central_index=centre, central_slice=volume[centre], central_mask=cmask,
        coronal_raysum=coronal,
    )


def modulation_figure(series: ScanSeries, *, title: str = "", wed_step: int = 1):
    """Three-panel figure: axial+contour, mA-on-scout, WED-on-scout. Returns a Figure."""
    import matplotlib.pyplot as plt

    p = compute_profiles(series, wed_step=wed_step)
    nz = p.z_index.size
    dx = (series.pixel_spacing_mm or (1.0, 1.0))[1]
    dz = series.slice_spacing_mm or series.slice_thickness_mm or 1.0
    fig, (ax0, ax1, ax2) = plt.subplots(
        1, 3, figsize=(13, 4.4), gridspec_kw={"width_ratios": [1, 1.5, 1.5]}
    )

    # --- Panel 0: axial slice + body contour -----------------------------------------
    ax0.imshow(p.central_slice, cmap="gray", vmin=-160, vmax=240)
    ax0.contour(p.central_mask, levels=[0.5], colors="#f5d800", linewidths=1.6)
    ax0.set_title(f"Body contour (slice {p.central_index})", fontsize=10)
    ax0.axis("off")

    z_mm = p.z_index * dz  # cranio-caudal position
    nx = p.coronal_raysum.shape[0]

    def _scout(ax, profile, colour, ylabel, unit):
        lo, hi = np.percentile(p.coronal_raysum, [5, 99])
        ax.imshow(p.coronal_raysum, cmap="gray", aspect="equal", origin="lower",
                  extent=(0, nz * dz, 0, nx * dx), vmin=lo, vmax=hi)
        axp = ax.twinx()
        axp.plot(z_mm, profile, color=colour, lw=1.8)
        axp.set_ylabel(ylabel, color=colour, fontsize=10)
        axp.tick_params(axis="y", colors=colour)
        vmax, vmin = np.nanmax(profile), np.nanmin(profile)
        if np.isfinite(vmax):
            axp.annotate(f"Max {ylabel.split()[0]} = {vmax:.0f}{unit}",
                         xy=(0.02, 0.92), xycoords="axes fraction", color=colour, fontsize=9)
            axp.annotate(f"Min {ylabel.split()[0]} = {vmin:.0f}{unit}",
                         xy=(0.02, 0.05), xycoords="axes fraction", color=colour, fontsize=9)
        # Deliberately not "cranio-caudal": the sign of the slice-location axis depends on
        # patient position and vendor convention, which this figure does not establish.
        ax.set_xlabel("position along the scan axis (mm)", fontsize=9)
        ax.set_yticks([])

    _scout(ax1, p.tube_current_ma, "#1f77b4", "mA", "")
    ax1.set_title("Tube-current modulation", fontsize=10)
    _scout(ax2, p.wed_cm, "#d62728", "WED (cm)", "")
    ax2.set_title("Water-equivalent diameter", fontsize=10)
    ax2.text(0.34, 0.92, r"$D_w = 2\sqrt{A_w/\pi}$", transform=ax2.transAxes,
             fontsize=11, color="#d62728", ha="left")

    if title:
        fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    return fig


def save_modulation_figure(series: ScanSeries, out_path: str | Path, *,
                           title: str = "", dpi: int = 300, wed_step: int = 1) -> Path:
    """Render and save the modulation figure as a publication-resolution PNG."""
    fig = modulation_figure(series, title=title, wed_step=wed_step)
    out = Path(out_path)
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return out


def organ_overlay_figure(series: ScanSeries, *, masks=None, volume=None,
                         slice_index: int | None = None,
                         max_organs: int = 14, title: str = ""):
    """Axial slice with TotalSegmentator organ masks overlaid. Returns a Figure.

    When ``masks`` is omitted the optional TotalSegmentator extra is run (``[ai]``);
    if it is not installed a :class:`~ctdose_core.organ.TotalSegmentatorUnavailable`
    is raised, so callers can show an install hint instead of failing hard.
    """
    import matplotlib.pyplot as plt
    from matplotlib import colormaps
    from matplotlib.patches import Patch

    if volume is None:
        volume = series.load_volume()
    if masks is None:
        from .organ import TotalSegmentatorUnavailable, segment_organs, totalsegmentator_available
        if not totalsegmentator_available():
            raise TotalSegmentatorUnavailable(
                "organ overlay needs the optional extra: pip install 'ctdose-core[ai]' "
                "(TotalSegmentator, Apache-2.0, inference only)."
            )
        masks = segment_organs(series)

    masks = {k: np.asarray(v, dtype=bool) for k, v in masks.items() if np.asarray(v).any()}
    items = sorted(masks.items(), key=lambda kv: -int(kv[1].sum()))[:max_organs]
    if not items:
        raise ValueError("no non-empty organ masks to display")

    if slice_index is None:
        per_z = np.zeros(volume.shape[0])
        for _, m in items:
            per_z += m.reshape(m.shape[0], -1).sum(axis=1)
        slice_index = int(np.argmax(per_z))

    fig, ax = plt.subplots(figsize=(6.4, 6.0))
    ax.imshow(volume[slice_index], cmap="gray", vmin=-160, vmax=240)
    cmap = colormaps.get_cmap("tab20")
    handles = []
    for i, (name, m) in enumerate(items):
        sl = m[slice_index]
        if not sl.any():
            continue
        colour = cmap(i % 20)
        rgba = np.zeros((*sl.shape, 4))
        rgba[sl] = (*colour[:3], 0.45)
        ax.imshow(rgba)
        ax.contour(sl, levels=[0.5], colors=[colour], linewidths=1.0)
        handles.append(Patch(color=colour, label=name.replace("_", " ")))
    ax.set_title(title or f"TotalSegmentator organs (slice {slice_index})", fontsize=11)
    ax.axis("off")
    if handles:
        ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.01, 0.5),
                  fontsize=7, frameon=False)
    fig.tight_layout()
    return fig
