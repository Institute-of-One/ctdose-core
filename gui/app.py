"""ctdose-core inspection GUI (Streamlit).

A fast, local verification tool that makes the library's *provenance* visible:
for a chosen CT series or Radiation Dose SR it shows CTDIvol, DLP, SSDE and
effective dose, each badged with where it came from (RDSR, DICOM tag, or physics
fallback), the DRL alerts, the RDSR-vs-header agreement, the tube-current / WED
figure, and (with the optional [ai] extra) a TotalSegmentator organ overlay with
an interactive slice slider.

Developer / verification front-end, not the product GUI. Depends on Streamlit
(optional `[gui]` extra); the MIT core has no GUI dependency.

Run:  python -m streamlit run gui/app.py
"""

from __future__ import annotations

import io as _io
from pathlib import Path

import streamlit as st

from ctdose_core.io import load_series
from ctdose_core.pipeline import analyze_series
from ctdose_core.rdsr import read_rdsr
from ctdose_core.scanners import AmbiguousScannerError

try:
    from ctdose_core.tables import conversion_regions
    REGIONS = list(conversion_regions())
except Exception:  # pragma: no cover
    REGIONS = ["Head", "Chest", "Whole abdomen"]

SOURCE_STYLE = {
    "rdsr": ("RDSR (recorded)", "#1a7f37", "#e6f4ea"),
    "dicom_tag": ("DICOM tag (recorded)", "#0b5cad", "#e7f0fb"),
    "dicom_text": ("DICOM text (recorded)", "#0b5cad", "#e7f0fb"),
    "physics_fallback": ("physics fallback (estimated)", "#b35c00", "#fdf0e3"),
    "derived": ("derived", "#5b616e", "#eef0f2"),
    "user": ("user", "#5b616e", "#eef0f2"),
}


def _badge(source: str) -> str:
    label, fg, bg = SOURCE_STYLE.get(source, (source, "#5b616e", "#eef0f2"))
    return (f"<span style='background:{bg};color:{fg};padding:2px 8px;border-radius:10px;"
            f"font-size:0.72rem;font-weight:600'>{label}</span>")


def _quantity_row(name: str, q: dict | None) -> None:
    if not q or q.get("value") is None:
        st.markdown(f"**{name}** — n/a")
        return
    c1, c2 = st.columns([1, 2])
    c1.metric(name, f"{float(q['value']):.3g} {q.get('unit', '')}")
    with c2:
        st.markdown(_badge(q.get("source", "")), unsafe_allow_html=True)
        if q.get("formula"):
            st.caption(q["formula"])
        for n in q.get("notes", []):
            st.caption("• " + n)


st.set_page_config(page_title="ctdose-core inspector", layout="wide")
st.title("ctdose-core — dose inspector")
st.caption("Every value is badged with its source. Estimated (fallback) values are orange.")

with st.sidebar:
    st.header("Input")
    path_str = st.text_input("CT series directory, or a Radiation Dose SR file")
    region = st.selectbox("Body region", REGIONS, index=min(1, len(REGIONS) - 1))
    use_rdsr = st.checkbox("Prefer RDSR when present", value=True)
    prefer_recorded = st.checkbox("Prefer recorded CTDIvol/DLP", value=True)
    measure_size = st.checkbox("Measure size (SSDE, loads pixels)", value=True)
    show_figure = st.checkbox("Figure: contour + mA + WED", value=True)
    show_organs = st.checkbox("Organ overlay (from precomputed masks)", value=False)
    organ_masks_dir = st.text_input(
        "Organ masks folder (run examples/segment_fast.py first)"
    ) if show_organs else ""
    flip_lr = st.checkbox("mask: flip L-R", value=False) if show_organs else False
    flip_ap = st.checkbox("mask: flip A-P (usually needed for TS)", value=True) if show_organs else False
    flip_z = st.checkbox("mask: reverse Z", value=False) if show_organs else False
    bowtie_sel = st.selectbox("Bowtie / scan FOV", ["(auto)", "small", "medium", "large"], index=0)
    bowtie = None if bowtie_sel == "(auto)" else bowtie_sel
    scanner_model = st.text_input("Scanner model override (optional)") or None
    drl_key = st.text_input("DRL key (optional, e.g. routine_chest)") or None
    run = st.button("Analyze", type="primary")


def _do_analysis() -> None:
    """Run all heavy work once and cache it in session_state."""
    path = Path(path_str.strip().strip('"'))
    if not path.exists():
        st.session_state["result"] = {"error": f"Path not found: {path}"}
        return

    # standalone Radiation Dose SR file?
    if path.is_file():
        rep = read_rdsr(path)
        if rep is not None:
            st.session_state["result"] = {"rdsr_report": rep}
            return

    try:
        series_obj = load_series(path)
        rep = analyze_series(
            series_obj, region=region, bowtie=bowtie, scanner_model=scanner_model,
            use_rdsr=use_rdsr, prefer_recorded=prefer_recorded,
            measure_size=measure_size, drl_key=drl_key,
        )
    except AmbiguousScannerError as exc:
        st.session_state["result"] = {"ambiguous": str(exc)}
        return
    except Exception as exc:  # pragma: no cover
        st.session_state["result"] = {"error": f"Analysis failed: {exc}"}
        return

    d = rep.to_dict()
    res: dict = {"d": d, "series": series_obj}

    if show_figure or show_organs:
        try:
            res["volume"] = series_obj.load_volume()
        except Exception:
            res["volume"] = None

    if show_figure:
        try:
            from ctdose_core.figures import modulation_figure
            s = d.get("series", {})
            fig = modulation_figure(series_obj, title=f"{s.get('manufacturer', '')} {s.get('model_name', '')}")
            buf = _io.BytesIO()
            fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
            res["modulation_png"] = buf.getvalue()
            import matplotlib.pyplot as plt
            plt.close(fig)
        except Exception as exc:  # pragma: no cover
            res["modulation_error"] = str(exc)

    if show_organs:
        mdir = (organ_masks_dir or "").strip().strip('"')
        if mdir and Path(mdir).is_dir():
            try:
                from ctdose_core.organ import load_masks_from_dir, organ_dose_profile
                masks = load_masks_from_dir(mdir, flip_lr=flip_lr, flip_ap=flip_ap, flip_z=flip_z)
                if masks:
                    res["masks"] = masks
                    res["organ_profile"] = [
                        w.to_dict() for w in organ_dose_profile(series_obj, masks=masks).weights
                    ]
                else:
                    res["organs_error"] = f"no .nii.gz masks found in {mdir}"
            except Exception as exc:  # pragma: no cover
                res["organs_error"] = str(exc)
        else:
            res["organs_need_precompute"] = True

    st.session_state["result"] = res


if run:
    with st.spinner("Analyzing..."):
        _do_analysis()

res = st.session_state.get("result")
if not res:
    st.info("Enter a path in the sidebar and press Analyze.")
    st.stop()

if res.get("error"):
    st.error(res["error"])
    st.stop()
if res.get("ambiguous"):
    st.warning(res["ambiguous"])
    st.info("Pick a Bowtie / scan FOV in the sidebar (or set a scanner model override) and Analyze again.")
    st.stop()

# --- standalone RDSR view --------------------------------------------------------------
if res.get("rdsr_report") is not None:
    rep = res["rdsr_report"]
    st.subheader("Radiation Dose SR")
    st.write(f"**{rep.manufacturer}** {rep.model_name} — {len(rep.acquisitions)} event(s)")
    if rep.total_dlp:
        _quantity_row("Total DLP", rep.total_dlp.to_dict())
    st.dataframe([{
        "event": i, "protocol": a.protocol, "type": a.acquisition_type,
        "CTDIvol (mGy)": float(a.ctdivol) if a.ctdivol else None,
        "DLP (mGycm)": float(a.dlp) if a.dlp else None,
        "phantom": a.phantom_type, "kVp": a.kvp,
    } for i, a in enumerate(rep.acquisitions, 1)], use_container_width=True)
    st.stop()

# --- full series analysis --------------------------------------------------------------
d = res["d"]
series = d.get("series", {})
st.subheader(f"{series.get('manufacturer', '?')} {series.get('model_name', '')}")
st.write(f"Region **{d.get('region')}** · scanner model **{d.get('scanner_model')}** · size **{d.get('patient_size')}**")

st.markdown("### Dose quantities")
q = d.get("quantities", {})
_quantity_row("CTDIvol", q.get("ctdivol"))
_quantity_row("DLP", q.get("dlp"))
_quantity_row("SSDE", (d.get("ssde") or {}).get("ssde") if d.get("ssde") else q.get("ssde"))
_quantity_row("Effective dose", q.get("effective_dose"))

col_a, col_b = st.columns(2)
with col_a:
    st.markdown("### DRL alerts")
    if not d.get("alerts"):
        st.success("No alerts.")
    for a in d.get("alerts", []):
        st.warning(f"{a.get('code', '')}: {a.get('message', '')}")
with col_b:
    st.markdown("### RDSR vs header")
    if d.get("rdsr"):
        st.write(f"RDSR present · agreement: `{d.get('source_agreement')}`")
        for name, cands in d.get("recorded_candidates", {}).items():
            st.caption(name)
            st.dataframe([{"value": c.get("value"), "unit": c.get("unit"), "source": c.get("source")}
                         for c in cands], use_container_width=True)
    else:
        st.info("No RDSR found beside this series.")

if d.get("warnings"):
    st.markdown("### Warnings")
    for w in d["warnings"]:
        st.caption("⚠ " + w)

# --- modulation figure (rendered once, shown from cache) -------------------------------
if show_figure and res.get("modulation_png"):
    st.markdown("### Figure (contour · mA · WED)")
    st.image(res["modulation_png"], use_container_width=True)
    st.download_button("Download figure (300 dpi PNG)", res["modulation_png"],
                       file_name="ctdose_figure.png", mime="image/png")
elif show_figure and res.get("modulation_error"):
    st.caption(f"figure unavailable: {res['modulation_error']}")

# --- organ overlay with an interactive slice slider ------------------------------------
if show_organs:
    st.markdown("### Organ segmentation (TotalSegmentator)")
    if res.get("organs_need_precompute"):
        st.info(
            "Run TotalSegmentator ONCE in a plain terminal (never inside this app -- on "
            "Windows nnU-Net multiprocessing conflicts with Streamlit), then paste the output "
            "folder into the sidebar:\n\n"
            "    pip install \"ctdose-core[ai]\"\n"
            "    python examples/segment_fast.py D:\\path\\to\\series\n\n"
            "It prints the masks folder; paste that into 'Organ masks folder' and press Analyze."
        )
    elif res.get("organs_error"):
        st.caption(f"organ overlay unavailable: {res['organs_error']}")
    elif res.get("masks"):
        masks = res["masks"]
        volume = res.get("volume")
        nz = next(iter(masks.values())).shape[0]
        import numpy as np
        per_z = np.zeros(nz)
        for m in masks.values():
            per_z += np.asarray(m, bool).reshape(nz, -1).sum(axis=1)
        default_slice = int(np.argmax(per_z))
        sl = st.slider("Slice", 0, nz - 1, default_slice)
        from ctdose_core.figures import organ_overlay_figure
        ofig = organ_overlay_figure(series_obj := res["series"], masks=masks, volume=volume, slice_index=sl)
        st.pyplot(ofig, use_container_width=True)
        obuf = _io.BytesIO()
        ofig.savefig(obuf, format="png", dpi=300, bbox_inches="tight")
        st.download_button("Download organ figure (300 dpi PNG)", obuf.getvalue(),
                           file_name=f"ctdose_organs_slice{sl}.png", mime="image/png")
        if res.get("organ_profile"):
            st.caption("Tube-current-weighted relative organ exposure")
            st.dataframe(res["organ_profile"], use_container_width=True)

with st.expander("Full JSON (provenance)"):
    st.json(d)
