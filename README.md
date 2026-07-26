# ctdose-core

Open, auditable CT dose surveillance from DICOM: CTDIvol, DLP, SSDE and effective
dose — **including the case where the DICOM dose attributes are missing**, which is
the norm in public archives and older clinical data.

Every number the library returns knows where it came from. A CTDIvol read from
`(0018,9345)` and a CTDIvol reconstructed from tube current and scanner physics are
not interchangeable, and a dose survey that mixes them silently is not defensible.

```python
from ctdose_core import analyze_series

report = analyze_series("path/to/series", region="Chest", drl_key="routine_chest")

print(report.ctdivol)                  # Quantity(7.562 mGy, source=physics_fallback)
print(report.ctdivol.formula)          # CTDIvol = nCTDIw * mAs_rot * k_OB / pitch
print(report.ssde)                     # Quantity(11.9 mGy, source=derived)

for a in report.alerts:
    print(a.severity.value, a.message)
```

A CT Radiation Dose SR filed beside the series is found and preferred automatically:

```python
report = analyze_series("path/to/series", region="Chest")

print(report.ctdivol.source)           # Source.RDSR
print(report.rdsr_match.method)        # 'protocol name'
print(report.source_agreement)         # RDSR vs image header, if both are present
```

## Why

Retrospective and public-archive CT dose work stalls on a simple problem: many series
carry no `CTDIvol` and no usable dose report, but do carry the full acquisition
description including a per-slice tube-current profile. `ctdose-core` reconstructs the
dose indices from that acquisition physics, states plainly that it has done so, and
keeps the reconstruction auditable end to end.

## What it does

| Module | Purpose |
| --- | --- |
| `io` | DICOM ingest with vendor-aware fallback chains (GE / Canon-Toshiba / Siemens / Philips private attributes) |
| `rdsr` | CT Radiation Dose SR (DICOM PS3.16 TID 10011–10013): recorded per-event CTDIvol, DLP and reference phantom |
| `geometry` | Over-beaming `k_OB`, over-ranging `L_rot`, tube-voltage scaling |
| `metrics` | CTDIvol, DLP, DLP-in-air — recorded value or physics fallback, with an explicit source ranking |
| `segmentation` | Body contour with no AI dependency (threshold + largest component + hole fill) |
| `ssde` | Water-equivalent / effective diameter, AAPM TG-204 conversion, SSDE |
| `effdose` | Effective dose by the DLP-in-air route or the ICRP-103 k-factor route |
| `drl` | Diagnostic reference levels and comparison |
| `alert` | Auditable alerts: metric, reference, excess, size band, provenance |
| `organ` | *(optional `[ai]`)* TotalSegmentator-driven organ-level dose weighting |
| `tcia` | Local archive inventory helpers |
| `survey` | Public-archive survey: recorded-dose availability and fallback error, by manufacturer |
| `pipeline` | `analyze_series()` — all of the above in one call |

### Where a dose value comes from

A recorded dose is used when one exists, and the library is explicit about which record
it used:

```
RDSR  >  image header (0018,9345)  >  free-text dose comment  >  physics reconstruction
```

The CT Radiation Dose SR wins because it is the scanner's own per-irradiation-event
record, written for exactly this purpose. It is *not* used blindly: an SR describes a
whole study, so the irradiation event is matched to the series by protocol name and then
by scanning length, and **when no event can be attributed unambiguously the recorded
value is not used at all** — a wrong event attaches a wrong CTDIvol, which is worse than
falling back to physics. Where both an SR and an image-header value exist, both are kept
in `report.recorded_candidates` and their difference reported in
`report.source_agreement`; a disagreement above 2 % is raised as a warning, because it
usually means the event match is wrong.

## Install

```bash
pip install ctdose-core            # core: pydicom, numpy, scipy
pip install 'ctdose-core[ai]'      # + TotalSegmentator for organ-level weighting
pip install 'ctdose-core[tcia]'    # + tcia_utils (optional; see below)
```

The public-archive survey works over the NBIA REST API with the standard library alone.
`tcia_utils` is used automatically when it is importable, but it is not required: its
transitive `idc-index` dependency pins `pandas<3` and does not build on every supported
Python.

Python 3.10–3.12.

## Command line

```bash
ctdose analyze SERIES_DIR --region Chest --drl-key routine_chest
ctdose analyze SERIES_DIR --region Chest --rdsr DOSE_SR.dcm      # explicit dose SR
ctdose analyze SERIES_DIR --region Chest --force-fallback --json # validation mode
ctdose inspect SERIES_DIR                # which dose attributes does this series have?
ctdose survey ARCHIVE_ROOT               # tag coverage across a local directory
ctdose survey --tcia --screen-per-vendor 100   # the public-archive survey (Layer 2)
ctdose models                            # scanner models in the coefficient table
ctdose models --regions                  # supported body regions
ctdose drl                               # the shipped reference levels
```

## The physics

When `CTDIvol (0018,9345)` is absent, it is rebuilt from the acquisition using the
CT-Expo formulation:

```
k_OB     = (N·(h_col + dz)) / (h_col·(N + dz))          over-beaming
L_rot    = h_rec/2 + h_col·(m_OR·p + b_OR)              over-ranging
CTDIvol  = nCTDIw · (mA · t_rot) · k_OB / p
DLP      = CTDIvol · (scan_range + L_rot)/10
DLP_air  = nCTDIair · (mA · t_rot) · k_OB · (scan_range + L_rot)/10
E        = DLP_air · f_mean · k_CT · (kV/kV_ref)² · (kV/kV_ref)^0.5
E        = DLP · k_CT · (kV/kV_ref)² · (kV/kV_ref)^0.5 · k     (ICRP-103 route)
```

Size correction follows AAPM TG-204/TG-220:

```
D_w   = 2·√((HU_mean/1000 + 1)·A_ROI/π)
SSDE  = CTDIvol · f(D_w)
f_head(D) = 1.9852·exp(−0.0486·D)
```

`nCTDIw`, `nCTDIair`, `k_CT`, `N`, `dz`, `m_OR`, `b_OR` and `kV_ref` are per scanner
series, body region and patient size — 149 usable combinations across 30 scanner
models from four vendors.

## Provenance of the coefficient tables

The constants are **not** typed in by hand. They are machine-extracted from a
validated MATLAB reference implementation (`the reference implementation`, LISIT), which is treated
as read-only and is never modified:

```bash
python tools/extract_matlab_tables.py --matlab-root /path/to/CT-ExposureDose
python tools/extract_drl_tables.py    --matlab-root /path/to/CT-ExposureDose
```

This regenerates `ctdose_core/data/*.json`. `tests/test_matlab_parity.py` then checks
the library against an *independent literal transcription* of the MATLAB expressions,
so a drift in the port fails the build rather than the review.

### Data-quality findings, reported not hidden

Machine extraction surfaced three defects in the reference implementation. None is
silently corrected:

1. **`GE LightSpeed VCT (medium BS)`, head mode** — the source reads
   `nctdiair = 0.191/0.07`, giving 2.73 mGy/mAs, roughly ten times any plausible
   free-in-air CTDI, while its own comment says `Ph=0.6`. The extracted value is kept
   as-is and carries a `quality_flags` entry; `analyze_series()` emits it as a warning.
   Only the DLP-in-air effective-dose route is affected; CTDIvol, DLP and SSDE are not.
2. **Paediatric chest CTDIvol DRL, 1–5 years** — the source literal is `141` mGy,
   inconsistent with the same examination's 11 (<1 y) and 15 (6–10 y). It is shipped as
   `14` with the original in `source_value_ctdivol_mgy` and a note asking for
   verification against the J-RIME publication before citation.
3. **`aplattab16`, the 44 cm AP+lateral row** — its conversion factor `0.84` duplicates
   the preceding row's, where the effective-diameter table gives `0.81` for the same
   effective diameter. Found automatically: the extractor cross-checks every
   measured-dimension table against `etab*` and reports rows that disagree by more than
   the tabulation precision. It is the only such row in all six tables. Retrievable via
   `tables.ssde_table_quality_findings()`.

A fourth, structural issue is made explicit rather than left to execution order: the
paediatric ICRP-103 branches never assign `ctf`, relying on the MATLAB global retaining
the previous branch's value. The extractor resolves this to the adult branch of the
same scanner and region and records `ct_factor_inherited_from`.

## Validation

Validation is in two layers, and they answer different questions.

### Layer 1 — is a recorded dose read correctly?

`tools/make_rdsr_layer1.py` parses real CT Radiation Dose SR objects from **four
vendors** and writes `results/rdsr_layer1.{json,csv}`. Two internal consistency checks
are computed per file and reported rather than assumed:

- **Σ(per-event DLP) vs the SR's own accumulated DLP** — two independently recorded
  quantities inside the same object;
- **declared irradiation-event count vs the number parsed** — read by a separate tree
  walk, not by the parser.

The test suite adds a **template-blind pydicom oracle**: an independent recursive sweep
of the whole SR content tree for every `113830` (CTDIvol), `113838` (DLP), `113813`
(accumulated DLP) and `113835` (phantom) item, compared against what the
template-navigating parser returned. If the parser ever drops a branch or picks a value
out of the wrong container, the two disagree.

### Layer 2 — what happens when there is no recorded dose?

`tools/run_tcia_survey.py` surveys public CT series from TCIA, stratified by
manufacturer and sampled round-robin across each vendor's collections so no single large
collection dominates. It writes `results/survey.{json,csv}` with:

- **(a) recorded-dose absence** — how often CTDIvol and a DLP record are missing from the
  image header, per vendor; plus *fallback feasibility*, i.e. whether the acquisition
  parameters the reconstruction needs are present at all. A series with neither cannot be
  surveyed by any method, and that is worth counting separately.
- **(b) fallback error** — for series that *do* carry CTDIvol, the recorded value is
  hidden, the physics reconstruction run, and the signed relative error reported as a
  **distribution**, per vendor. It is never reduced to a correction factor.

Every exclusion is counted (`fallback_exclusions`) rather than dropped silently, and each
series' licence is carried into the results so a data-availability statement can be
written from them.

```bash
pytest                                                    # offline; survey uses a stub archive
pytest -m "not rdsr_samples"                              # skip the third-party-data tests
CTDOSE_RDSR_SAMPLES=/path/to/rdsr pytest                  # + real vendor RDSR objects
python tools/make_rdsr_layer1.py /path/to/rdsr            # Layer 1 table
python tools/run_tcia_survey.py --screen-per-vendor 100   # Layer 2 survey (network)
```

### Unit and reference-parity tests

- **Closed form.** The head SSDE conversion is checked against
  `1.9852·exp(−0.0486·D)`. The water-equivalent diameter is checked against synthetic
  phantoms with an analytically known answer: a uniform 0 HU disc of radius *r* must
  return `D_w = 2r`, and doubling the attenuation must scale it by exactly `√2` — end
  to end, through the segmentation.
- **Reference parity.** Every one of the 149 usable coefficient rows is evaluated, and
  a cross-vendor sample is compared against the literal MATLAB transcription at
  `rel=1e-12`.
- **Table integrity.** Completeness of every usable row, physical plausibility bounds,
  monotonicity and exponential form of the TG-204 tables, monotonicity of the
  paediatric DRLs with age.
- **Recorded vs reconstructed.** `--force-fallback` runs the physics estimate on series
  that *do* carry the tag; `metrics.fallback_error()` quantifies the difference. This
  is how the fallback's validity is measured rather than assumed.
- **RDSR integration.** Source priority, event matching, the refusal to attribute an
  ambiguous event, and the RDSR-vs-header cross-check are all tested against
  *synthesised* SR objects, so they run in CI without third-party data.

### Third-party sample data

Layer 1 runs against the OpenREM sample RDSR objects (**GPL-3.0**). They are **not**
vendored into this MIT repository. Point `CTDOSE_RDSR_SAMPLES` at a directory holding
them — e.g. `pip download openrem --no-deps --no-binary :all:`, then extract
`openrem/remapp/tests/test_files/CT-*.dcm`. The tests skip cleanly when it is unset, so
CI needs no third-party data. Only *derived measurements* appear in
`results/rdsr_layer1.*`, with the source attributed in the file's provenance block.

## Test fixture

`data/DOSE_TARO/` holds a small subset of a **dummy patient** phantom acquisition
(GE LightSpeed VCT, 120 kV, chest–pelvis, tube-current modulated). It carries no PHI;
institution, accession, station, birth date and study identifiers are stripped and UIDs
regenerated by `tools/make_fixture.py`. It is exactly the case this library exists for:
per-slice tube current present, `CTDIvol` and DLP absent.

## Optional AI layer

`ctdose_core.organ` uses TotalSegmentator (Apache-2.0, **inference only**) to obtain a
body contour and organ masks, then weights each organ by the tube current over its
longitudinal extent:

```
w_o = [Σ_{z∈Z_o} n_o(z)·I(z) / Σ_{z∈Z_o} n_o(z)] / [mean over the scan of I(z)]
```

`w_o` is an **exposure-weighting index, not an absolute organ dose**. Converting it to
mGy needs published Monte-Carlo or measurement normalisation coefficients;
`scale_to_absolute()` applies them only when the caller supplies both the coefficients
and their citation, and refuses otherwise.

The core never imports TotalSegmentator. Without the extra, everything except
`organ.segment_organs()` works, and patient size falls back to the threshold-based
contour.

## What the survey found, and what it means for the method

Three things the Layer-2 survey established that change how the reconstruction should be
read. All are in `results/survey.json`; the numbers there are the ones to cite — the
prose below describes the shape of the result, not a substitute for it.

1. **Recorded-dose availability is a vendor property, not an archive property.** In a
   sample stratified across each vendor's collections, the CTDIvol absence rate differs
   by tens of percentage points between manufacturers within the same archive — for one
   vendor no sampled series carried an image-header CTDIvol at all. A single pooled
   "public data lacks CTDIvol" figure would hide that. Quote the per-vendor rates with
   their `n_series_readable` denominators, not the pooled one.
2. **The reconstruction's error is dominated by whether total collimation was recorded,
   not by the dose model.** The over-beaming factor grows roughly as `1/h_col` once the
   collimation falls below the scanner's reference beam width, so a header that omits
   collimation — leaving it to be rebuilt from table feed, or in the worst case from
   slice thickness — widens the error far more than the choice of scanner coefficients
   does. The error distribution is therefore reported stratified by
   `fallback_error.by_collimation_provenance`, and pooling the two strata would
   misattribute a missing-metadata problem to the physics.

3. **The two are confounded, so read the cross-stratum — and mind who is missing from
   it.** Collimation availability is itself vendor-dependent:
   `fallback_error.vendor_by_collimation_provenance` shows the split, and in this sample
   one vendor contributed **no** series with a recorded collimation, so it is absent from
   the headline stratum entirely. Its apparently poor per-vendor error is a
   metadata artefact, not a vendor-specific failure of the dose model — and equally, the
   method is **unvalidated** for that vendor on this archive. Say both.

   The number to quote as *the method's accuracy* is
   `fallback_error.diagnostic_and_collimation_measured`: diagnostic acquisitions
   (recorded CTDIvol ≥ 0.5 mGy) whose header records collimation. Everything wider than
   that stratum is measuring the archive's metadata quality, not the dose model.

Two smaller data-quality findings, both handled by counting rather than averaging in:

- At least one vendor series carries a syntactically valid but nonsensical CTDIvol (a
  large negative binary value). Values outside `PLAUSIBLE_CTDIVOL_MGY` are excluded and
  counted in `fallback_exclusions.implausible_recorded_ctdivol`.
- One series' recorded CTDIvol belongs to a localiser (0.05 mGy). Comparing that against
  a reconstruction of the diagnostic series compares two different irradiations and
  produced a five-figure relative error; hence the `DIAGNOSTIC_CTDIVOL_MGY` threshold.
  The case is kept in `overall` and in the per-case rows, never silently dropped.

## Scope and limitations

- Effective dose from DLP is a **population** quantity for a reference phantom. It is
  not an individual patient's dose and must not be reported as one.
- A DRL applies to the median of a representative patient sample, not to one
  examination. Every DRL alert carries that caveat and the patient's size band.
- The scanner coefficient table covers the 30 models in the reference implementation.
  Other scanners raise `UnknownScannerError` rather than substituting a similar model.
  `results/survey.json` → `tag_absence.unmatched_models` lists, in frequency order, the
  model names a public archive actually contains that the table does not cover.
- Model matching never crosses vendors, and the acceptance threshold
  (`scanners.MIN_MATCH_SCORE`) is calibrated against the 40 distinct DICOM model names
  observed in the survey: every correct match there scores ≥ 0.45, every spurious one
  ≤ 0.38. A family name that does not identify a coefficient row — `Aquilion`, the most
  common Canon/Toshiba name in the archive — is reported as unknown rather than costed
  against `Aquilion Prime`.
- Bowtie / scan-field variants (`small`/`medium`/`large BS`) cannot be resolved from
  the DICOM header; pass `bowtie=` explicitly where it matters. The survey reports the
  median across candidates plus an optimistic bound
  (`fallback_error.best_case_if_scan_field_known`) for what the method could achieve if
  the scan field were recorded.
- A CT Radiation Dose SR describes a study, not a series. When its irradiation event
  cannot be matched to the series unambiguously, no recorded value is taken from it.
- The shipped DRL set is Japan DRLs 2015. Register newer or local sets with
  `drl.register_drl_set()`.

## Citation and disclosures

- **Affiliation:** Institute of One, LISIT Co., Ltd.
- **Licence:** MIT.
- **Data:** the bundled fixture is a dummy-patient phantom acquisition, not patient
  data. TCIA collections accessed through `ctdose_core.tcia` remain subject to the TCIA
  Data Usage Policy and each collection's own licence; record the collection name,
  licence and access date in any publication.
- **AI disclosure:** TotalSegmentator (Apache-2.0) is used for inference only, in the
  optional extra. Generative AI (Claude) was used as a development aid.
- **Not a medical device.** For research and quality-assurance use.
