# Reproducing the image figures

Figures 4–6 of the manuscript and the screenshots in the README are generated from
**publicly available CT images**. No DICOM is redistributed with this repository — the
images stay where they are published, and this page tells you how to fetch exactly the
same ones.

Everything else (Figures 1–3, `results/survey.json`, `results/rdsr_layer1.json`) is
generated from data described in the manuscript's Data section and needs nothing from here.

---

## 1. The dataset

| | |
|---|---|
| **Archive** | The Cancer Imaging Archive (TCIA) |
| **Collection** | Low Dose CT Image and Projection Data (`LDCT-and-Projection-data`) |
| **DOI** | [10.7937/9npb-2637](https://doi.org/10.7937/9npb-2637) |
| **Licence** | Creative Commons Attribution 4.0 International (CC BY 4.0) |
| **Data citation** | Moen TR, Chen B, Holmes DR III, Duan X, Yu Z, Yu L, Leng S, Fletcher JG, McCollough CH. Low-dose CT image and projection dataset. *Med Phys*. 2021;48(2):902–911. doi:10.1002/mp.14594 |

The collection is free to download and requires no data-use agreement beyond the CC BY
attribution. It is used here for two independent reasons:

1. its images are real, tube-current-modulated clinical CT — which is what the figures
   need to be honest about;
2. its **clinical data reports carry a per-series CTDIvol and SSDE that the image headers
   do not**, which makes it a genuine external reference for the reconstruction rather
   than a self-consistency check.

## 2. The exact series

| | Figures | Validation |
|---|---|---|
| **Subject** | `L273` | `L277` |
| **Modality** | CT | CT |
| **Series description** | Full Dose Images | Full Dose Images |
| **Scanner** | Siemens Healthcare SOMATOM Definition AS+ | Siemens Healthcare SOMATOM Definition AS+ |
| **Acquisition** | abdomen, **120 kVp**, 5 mm reconstructed slices, tube-current modulated | abdomen, **100 kVp**, 5 mm reconstructed slices, tube-current modulated |
| **Images in series** | 165 | 214 |
| **Patient age** | 28 y | 71 y |
| **Series Instance UID** | `1.2.840.113713.4.100.1.2.310086300212904160703482251540856` | `1.2.840.113713.4.100.1.2.262990409213147100927629505622692` |

The two tube voltages are the point of the pairing: L273 was acquired at the voltage the
coefficient table is tabulated at, so the tube-voltage correction is inert and the case
tests the reconstruction itself; L277 was not, so it tests the correction. See
§3.6 of the manuscript.

The machine-readable version of this table is
[`paper/demonstration_cases.json`](../paper/demonstration_cases.json). It holds the case
*identity* only — the reference CTDIvol and SSDE are read at run time from the collection's
own clinical data report, so the published value stays the single source of truth.

## 3. Downloading

### Images

1. Open the collection page: <https://doi.org/10.7937/9npb-2637>.
2. Search the collection for the subject ID (`L273`, `L277`) and add the **Full Dose
   Images** series to your cart. Alternatively search directly by the Series Instance UID
   above, which removes any ambiguity about which series you got.
3. Download the `.tcia` manifest and open it with the free
   [NBIA Data Retriever](https://wiki.cancerimagingarchive.net/display/NBIA/Downloading+TCIA+Images).
4. Extract to any directory. One directory per series.

If you would rather script it, the NBIA REST API used by `ctdose_core.survey.TciaClient`
will fetch a series by UID with no credentials:

```python
from ctdose_core.survey import TciaClient
TciaClient().download_series(
    "1.2.840.113713.4.100.1.2.310086300212904160703482251540856",
    dest="data/L273-FULL-DOSE",
)
```

### The clinical data report

The per-series CTDIvol and SSDE live in the collection's *clinical data* download —
`Abdomen_v9 Mar 2023.xlsx`, sheet `Abdomen`, columns `CTDIvol_(mGy)` and `SSDE_(mGy)`,
matched on `SeriesUID`. It is linked from the same collection page under "Data Access".
It is not redistributed here.

## 4. Pointing the repository at your copy

Edit the `local_path`, `mask_path` and `clinical_report` entries in
[`paper/demonstration_cases.json`](../paper/demonstration_cases.json). Nothing else in the
repository depends on where you put the data.

## 5. Regenerating

```bash
# organ masks for Figures 5 and 6 (TotalSegmentator, Apache-2.0, inference only)
pip install "ctdose-core[ai]"
python examples/segment_fast.py <L273 dir> --hires --out <mask dir>

# all six figures + paper/figures/figure_data.json
python paper/figures/make_figures.py

# the numbers the manuscript quotes, and the check that it quotes them correctly
python tools/extract_manuscript_values.py
pytest tests/test_manuscript_consistency.py
```

`make_figures.py` reads the case file for the series and mask directories. If the images
are not present it says so and generates Figures 1–3 only, which need no images.

Figure generation is deterministic: any jitter in the figures is derived from a SHA-256 of
the point's identity rather than from a random seed, so two runs on two machines produce
byte-comparable numbers in `figure_data.json`.

## 6. What each figure uses

| Figure | Source | Needs images? |
|---|---|---|
| 1 — RDSR vs image-tag agreement | `results/rdsr_layer1.json` | no |
| 2 — recorded-dose availability by manufacturer | `results/survey.json` | no |
| 3 — reconstruction error distribution | `results/survey.json` | no |
| 4 — tube-current modulation and water-equivalent diameter | L273 | **yes** |
| 5 — TotalSegmentator organ overlay | L273 + masks | **yes** |
| 6 — relative organ exposure index | L273 + masks | **yes** |

## 7. What is *not* in this repository, and why

* **No DICOM.** The images are published under CC BY at TCIA; redistributing a copy would
  add a second, un-versioned origin for data that already has a citable one.
* **No clinical data report.** Same reason, and the reference dose values must come from
  the published file rather than from a transcription that could silently drift.
* **No private or internally anonymised patient data.** Every image in the manuscript, the
  README and this repository's figures comes from the public collection above. Earlier
  drafts used an internal series; it has been removed, and nothing generated from it
  remains.
* **No GPL-licensed RDSR sample objects.** The Layer-1 validation runs against the OpenREM
  test corpus, which is GPL; the test skips unless `CTDOSE_RDSR_SAMPLES` points at a local
  copy. See the manuscript's Data section.
