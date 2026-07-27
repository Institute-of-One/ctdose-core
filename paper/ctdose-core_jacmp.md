# Recorded, reconstructed or unrecoverable: auditable CT dose surveillance across four manufacturers on public imaging archives

**Shuji Yamamoto**

Institute of One, LISIT Co., Ltd., Tokyo, Japan

**Corresponding author:** Shuji Yamamoto. Email: yamamoto@lisit.jp

**Running title:** Auditable CT dose surveillance on public data

**Article type:** Research Article

**Section:** Diagnostic and Interventional Imaging Physics

**Keywords:** computed tomography; CTDIvol; size-specific dose estimate; radiation dose structured report; open data; reproducibility

---

## Abstract

**Purpose:** Retrospective CT dose surveillance on public imaging archives is limited by a rarely quantified problem: the recorded dose index is frequently absent from the image header. We built an open-source, auditable dose engine that reads a recorded dose index where one exists and reconstructs it from acquisition physics where one does not, and used it to measure how much of a public archive is actually surveyable.

**Methods:** We developed `ctdose-core`, an open-source Python package released under the MIT License, to compute CTDIvol, dose-length product (DLP), size-specific dose estimate (SSDE) and effective dose, returning every value with its formula, its inputs and whether it was recorded or reconstructed. A vendor-neutral parser reads the CT Radiation Dose Structured Report (RDSR) from DICOM coded content items only; where no recorded index exists, CTDIvol is rebuilt from tube current, rotation time, collimation and pitch using CT-Expo-style normalised coefficients. SSDE follows AAPM TG-204 from an automatically contoured water-equivalent diameter, and an optional layer weights TotalSegmentator organ masks by the tube-current modulation. Validation had three layers: 14 real RDSR objects from four manufacturers; 400 public CT series from The Cancer Imaging Archive (TCIA), 100 per manufacturer across 92 collections; and two subjects from a public collection that publishes per-series CTDIvol and SSDE values not contained in the image headers. Every image reproduced here derives from publicly available TCIA data, and no imaging is redistributed.

**Results:** Layer 1 correctly identified and parsed all 14 RDSR objects and rejected one secondary-capture dose screen; they spanned 4 manufacturers, 9 scanner models and 67 irradiation events, and two independent consistency checks passed on 14 of 14 files. In Layer 2, CTDIvol was absent from the image header in 206 of 400 series (51.5%), at manufacturer-dependent rates from 24.0% to 100.0%. Of those 206, 84 (40.8%) retained enough metadata to reconstruct one, again manufacturer-dependently (GE 73.0%, Canon/Toshiba 37.5%, Philips 5.9%, Siemens 0%); by either route 278 of 400 series (69.5%) were dose-recoverable. Restricted to diagnostic acquisitions whose header records the total collimation (n = 23), the reconstruction had a median relative error of +18.9%; error was dominated by whether collimation was recorded (median +20.5% when recorded, +156.4% when reconstructed) rather than by the dose model. Against published per-series values it reached +1.4% on a subject whose acquisition was adequately described and −33.9% on one whose pitch was unrecorded.

**Conclusions:** Recorded CT dose availability was strongly associated with manufacturer in this TCIA sample, and roughly a third of series could not be dose-surveyed by any method we implemented. A physics reconstruction closes that gap unevenly — most of one manufacturer's dose-less series, none of another's — with an accuracy governed chiefly by the completeness of the acquisition metadata. Carrying provenance with every value is what makes such a survey defensible.

---

## 1. Introduction

In most surveyed countries computed tomography is the largest single contributor to the population's collective medical exposure [1], and dose optimisation rests on measuring the dose indices that describe an examination reliably and at scale. Routine surveillance is built on the volume CT dose index (CTDIvol) and the dose-length product (DLP), both defined against a standard cylindrical phantom of 16 cm or 32 cm diameter [8]: phantom-referenced indices of scanner output, not absorbed patient dose. Because they describe the phantom rather than the patient, AAPM Task Group 204 introduced the size-specific dose estimate (SSDE), correcting CTDIvol by the patient's water-equivalent diameter [2, 3], and national bodies publish diagnostic reference levels (DRLs) for comparison against a facility's median values [16, 17].

Modern scanners record these indices in a DICOM Radiation Dose Structured Report (RDSR) [7], and dose-management systems such as OpenREM harvest them at scale from clinical PACS [15]. That infrastructure works inside an institution but does not transfer to the public archives on which most reproducible imaging research is now built: de-identified collections generally carry no dose structured report, and their image headers often omit CTDIvol as well. A study relating image findings to the dose indices of the examinations that produced them therefore has no recorded value to work from.

That much is familiar to anyone who has tried it; what is missing is a measurement of how bad the problem is. AAPM Report 246 catalogues the DICOM attributes organ dose estimation requires and recommends that more be standardised [4], but neither it nor, to our knowledge, any later study reports how often those attributes survive into a public archive. A search of PubMed and Scopus (July 2026; *computed tomography*, *CTDIvol* or *dose index*, *public archive* or *TCIA*, and *availability* or *completeness*) returned no such quantification, so we report ours as the first we are aware of, not the first that exists.

Where the recorded index is missing there is a physically principled way out: the acquisition description public archives *do* retain — tube voltage, slice-wise tube current, rotation time, collimation, pitch and slice geometry — suffices to reconstruct CTDIvol from a scanner's normalised CTDI coefficients, the CT-Expo approach [9]. Such a reconstruction is only as good as its metadata, and its uncertainty must be measured rather than assumed. Two requirements follow: a reconstructed and a recorded index are not interchangeable, so provenance must travel with every value; and the implementation must be open, because a dose figure a reader cannot re-derive is not a measurement.

This work reports an open-source implementation meeting those requirements and the survey it made possible. Can a vendor-neutral parser read recorded dose correctly from real RDSR objects across manufacturers? How much of a public CT archive carries a recorded dose, or enough metadata to reconstruct one, and does this depend on the manufacturer? And how accurate is the reconstruction — against the scanner's own recorded value and against an externally published one — and what governs its error?

## 2. Materials and methods

### 2.1 Open dose engine and provenance model

`ctdose-core` has a functional API and a command-line interface, and depends only on `pydicom`, `numpy` and `scipy`. Every quantity it returns carries the value, its unit, the formula that produced it, that formula's inputs, a literature reference where one applies, and a `Source` recording whether it was read from an RDSR or a DICOM attribute, parsed from a free-text dose comment, reconstructed from physics, or derived. A survey can therefore report the recorded/reconstructed split without re-tracing the computation.

Where a value could come from more than one place, an explicit ranking applies:

> RDSR > image header attribute (0018,9345) > free-text dose comment (0040,0310) > physics reconstruction.

Lower-ranked values are retained alongside the winner and their difference reported, because a disagreement between an RDSR and an image-header value usually means the wrong irradiation event was attributed to the series rather than a genuine dose discrepancy.

### 2.2 Vendor-neutral RDSR parsing

The parser is written against the coded content items of DICOM PS3.16 templates TID 10011–10013 [7] — the `DCM` coding scheme and its documented code values — rather than any vendor's layout. It walks the SR content tree and extracts, per irradiation event, the mean CTDIvol (code 113830), the DLP (113838), the CTDIw reference phantom (113835) and the acquisition technique, plus the study-level accumulated DLP (113813) and declared event count (113812).

An RDSR describes a whole examination, not one reconstructed series, so attributing an event to a series is an explicit step, matched on acquisition protocol name first and scanning length second. When no event can be attributed unambiguously, no recorded value is taken and the pipeline falls back to the image header or to physics: attaching another event's CTDIvol is a worse error than declining to attribute one. A CTDIvol read from an RDSR carries its own reference phantom, which overrides the body-region heuristic when the SSDE conversion table is selected.

### 2.3 Physics reconstruction when no recorded dose exists

When no recorded CTDIvol is available, it is reconstructed following the CT-Expo formulation [9]:

$$k_{OB} = \frac{N\,(h_{col} + d_z)}{h_{col}\,(N + d_z)}, \qquad
L_{rot} = \frac{h_{rec}}{2} + h_{col}\,(m_{OR}\,p + b_{OR}),$$

$$\mathrm{CTDI_{vol}} = n\mathrm{CTDI_w} \cdot (I \cdot t_{rot}) \cdot \frac{k_{OB}}{p}, \qquad
\mathrm{DLP} = \mathrm{CTDI_{vol}} \cdot \frac{L_{scan} + L_{rot}}{10},$$

where $I$ is the mean tube current over the series, $t_{rot}$ the rotation time, $h_{col}$ the total collimation, $p$ the pitch, $h_{rec}$ the reconstructed slice thickness and $L_{scan}$ the reconstructed scan range in mm. The over-beaming factor $k_{OB}$ corrects for the penumbra outside the nominal beam relative to the reference beam width $N$ at which the coefficient was measured; $L_{rot}$ accounts for helical over-ranging. Effective dose comes either from the free-in-air DLP and an organ-conversion factor or from DLP and an ICRP 103 $k$-factor [6], by the route the coefficient row prescribes, both carrying the tube-voltage corrections $(kV/kV_{ref})^2$ and $(kV/kV_{ref})^{0.5}$. It is a population quantity for a reference phantom, not an individual patient's dose.

Each coefficient row is tabulated at one tube voltage $kV_{ref}$, nearly always 120 kV, whereas archive series span a range and tube output per mAs rises steeply with voltage. We therefore scale the tabulated $n\mathrm{CTDI_w}$ by $(kV/kV_{ref})^2$ — the correction the reference implementation applies on its effective-dose route — whenever the series' voltage is known and differs from $kV_{ref}$. It is recorded in the value's provenance and can be switched off, which is how the with- and without-correction figures of §3.3 and §3.6 were obtained from identical inputs. The exponent models the output–voltage relationship to first order rather than measuring it per scanner; §3.3 reports where it helps and where it does not.

The coefficients $n\mathrm{CTDI_w}$, $n\mathrm{CTDI_{air}}$, $N$, $d_z$, $m_{OR}$, $b_{OR}$ and $kV_{ref}$ are specific to the scanner series, body region and patient size. They were machine-extracted from an in-house MATLAB reference implementation rather than transcribed by hand, yielding 149 usable rows covering 30 scanner models from four manufacturers. The extraction scripts ship with the package, and the port is verified against an independent literal transcription of the reference expressions to a relative tolerance of $10^{-12}$ on every row.

Matching a DICOM `ManufacturerModelName` to a row is one-to-many: the header does not record the bowtie filter, scan field of view or acquisition mode the coefficients distinguish. Matching is by token overlap, never crosses manufacturers, and rejects weak matches — a family name identifying no row is reported as unknown rather than costed against a nearby model. Where the residual ambiguity is the bowtie, one attribute resolves it: the bowtie is chosen with the scan field, and the field size *is* recorded, as Data Collection Diameter (0018,0090) or failing that Reconstruction Diameter (0018,1100). We map a field of at most 300 mm to a small (head) bowtie, at most 450 mm to a medium (body) bowtie and anything larger to a large-body bowtie. These thresholds are a pragmatic tripartition, not a vendor-published mapping, so the inference is recorded as an assumption in the provenance and in the released data, and an explicit bowtie overrides it. Otherwise the median across admissible rows is reported, every candidate retained.

### 2.4 Size-specific dose estimate

The water-equivalent diameter is computed from an automatically extracted body contour as $D_w = 2\sqrt{(\overline{\mathrm{HU}}/1000 + 1)\,A_{ROI}/\pi}$ [3], with $\overline{\mathrm{HU}}$ the mean Hounsfield value inside the contour and $A_{ROI}$ its area. The contour uses a fixed attenuation threshold, largest-connected-component selection and hole filling, so it needs no deep-learning dependency and is deterministic; the table and detached support hardware are excluded, interior gas retained as the definition requires. SSDE is $\mathrm{CTDI_{vol}} \cdot f(D_w)$ with $f$ interpolated from the TG-204 tables for the applicable phantom [2], or the closed-form head conversion $f = 1.9852\exp(-0.0486 D_w)$ where that applies [5]. The implementation is verified against synthetic phantoms with an analytically known answer: a uniform 0 HU disc of radius $r$ must return $D_w = 2r$ through the segmentation, and doubling the attenuation must scale it by exactly $\sqrt{2}$.

### 2.5 Deep-learning organ layer

An optional extra uses TotalSegmentator (Apache-2.0) [13], for inference only, to obtain organ masks. We then implement the established organ-specific weighted CTDIvol of Khatonabadi et al. [10] and Tian et al. [11]. Given the slice-wise tube current $I(z)$ from (0018,1151) and an organ occupying slices $Z_o$ with per-slice voxel counts $n_o(z)$:

$$\bar{I}_o = \frac{\sum_{z \in Z_o} n_o(z)\,I(z)}{\sum_{z \in Z_o} n_o(z)}, \qquad \bar{I} = \frac{1}{|Z|}\sum_{z \in Z} I(z), \qquad (\mathrm{CTDI_{vol}})_{o} = \frac{\bar{I}_o}{\bar{I}}\,\mathrm{CTDI_{vol}}.$$

$\bar{I}_o/\bar{I}$ is the ratio Tian et al. denote $mA_{\mathrm{organ,weighted}}/mA_{\mathrm{scan}}$ and $(\mathrm{CTDI_{vol}})_{o}$ their organ-specific weighted CTDIvol; Khatonabadi et al. call the same construction a regional CTDIvol [10, 11]. We adopt their terminology rather than coining our own.

Those authors complete the chain to absorbed organ dose as $D_o = h_o \cdot (\mathrm{CTDI_{vol}})_{o}$, with $h_o$ a CTDIvol-normalised, size-dependent coefficient from Monte-Carlo simulation [11]. **We stop one step short of that.** $(\mathrm{CTDI_{vol}})_{o}$ is a dose index in mGy, not an absorbed organ dose, and converting it needs $h_o$ for the matched scanner, protocol and phantom, which we lack for these series. The software applies published coefficients only when the caller supplies both the coefficients and their citation, so an absolute organ dose can never be produced without an attributable source. Work that does complete the chain, driving a Monte-Carlo simulation from the same DICOM modulation profile, reports agreement with measurement within about ±10% for most abdominal organs [12]. The core package never imports TotalSegmentator; without the extra, everything but organ segmentation works.

### 2.6 Auditable DRL comparison

Measured indices are compared against diagnostic reference levels; each comparison emits an alert stating the metric, the reference set and examination, the numeric excess, the ratio, the patient size band and the provenance of the measured value, with no classifier score. Because a DRL applies to the median of a representative sample rather than an individual examination, that caveat rides on every alert.

### 2.7 Data

**Layer 1 (recorded-dose parsing).** The RDSR validation set is the CT dose structured report sample collection distributed with OpenREM (GPL-3.0) [15]: 15 DICOM objects from four manufacturers, 14 of them CT Radiation Dose Structured Reports. These third-party files are not redistributed with our software; the tests read them from a user-supplied directory and skip cleanly when absent, and only the measurements read out of them appear in our results.

**Layer 2 (public-archive survey).** CT series were drawn from The Cancer Imaging Archive [14] through its public NBIA REST interface, with no credentials and no write access, selected by DICOM `Manufacturer` and sampled round-robin across each manufacturer's collections so no single collection could dominate the rate; the sampling seed, sample sizes and access timestamps are in the released results. Each series is identified there by Series Instance UID and by the licence the archive states — Creative Commons Attribution 3.0 and 4.0 and their non-commercial variants, with 16 of 400 carrying no licence label in the archive index. All collections used are publicly available and de-identified.

**Demonstration and validation series.** The images behind the modulation, contour and organ figures and behind §3.6 are two subjects of the public TCIA collection *Low Dose CT Image and Projection Data* (`LDCT-and-Projection-data`, doi:10.7937/9npb-2637, CC BY 4.0) [18], both abdominal CT on a Siemens Healthcare SOMATOM Definition AS+ with tube-current modulation and both from the "Full Dose Images" series: **L273** (120 kVp, 165 images) provides Figures 4–6 and **L277** (100 kVp, 214 images) is the second validation case. The collection was chosen because it publishes a **per-series CTDIvol and SSDE in a clinical data report its image headers do not carry** — an external reference for a reconstruction built only from the acquisition description — and because its subjects differ in tube voltage, separating the reconstruction from the correction applied to it.

Neither the DICOM nor the clinical data report is redistributed. Exact Series Instance UIDs and retrieval instructions are provided in `docs/REPRODUCING_FIGURES.md` and the accompanying machine-readable result files; the reference dose values are read at run time from the published report rather than transcribed. No private or internally anonymised patient images are used in this work.

### 2.8 Reproducibility

Every number reported here is written by a script into machine-readable result files that ship with the software, and the manuscript quotes those files rather than restating computed values. Automated tests re-derive each quoted figure from them, recompute every summary statistic from the rows it summarises, and assert that the image figures came from a public, identified series, so neither a stale result file nor a hand-edited number can pass continuous integration. The figures are generated by script rather than drawn by hand.

## 3. Results

### 3.1 Layer 1: recorded dose is read correctly across manufacturers

Of 15 sample objects, 14 parsed as CT Radiation Dose Structured Reports; the fifteenth is a secondary-capture dose screen and was correctly rejected. The 14 span 4 manufacturers (GE, Siemens, Philips, Canon/Toshiba) and 9 scanner models, and contain 67 irradiation events, of which 44 carry a CTDIvol and 44 a DLP. Recorded CTDIvol spans 0.14–222.59 mGy and DLP 1.2–890.26 mGy·cm. Both the 16 cm head and the 32 cm body reference phantoms are represented, and a single study can mix them (Figure 1A).

Two internal consistency checks were computed per file and reported rather than assumed: the per-event DLPs summed to the accumulated DLP recorded in the same object within 1% in 14 of 14 comparable files (Figure 1B), and the number of irradiation events parsed equalled the number the report declares in 14 of 14 files. A template-blind sweep of the whole SR content tree — every occurrence of the relevant code values, wherever it sits — returned exactly the values the template-navigating parser returned for CTDIvol, DLP, accumulated DLP and phantom code in every file. That sweep is an independent oracle, agreeing only if the parser has neither dropped a branch nor read a value from the wrong container; it also caught one branch the parser was skipping, the phantom code attached to the accumulated DLP rather than to an event.

### 3.2 Layer 2a: recorded dose availability in a public archive

Four hundred CT series were screened, 100 per manufacturer, from 92 collections. CTDIvol was absent from the image header in 206 of 400 series overall (51.5%), but the pooled figure conceals the dominant effect (Figure 2A): the absence rate was 100.0% for GE, 48.0% for Siemens, 34.0% for Philips and 24.0% for Canon/Toshiba. Within one archive, recorded-dose availability was strongly associated with manufacturer ($\chi^2$(3) = 137.2, nominal p < 0.001, Cramér's V = 0.59). Because series sampled from the same collection may not be independent, the nominal p-value should be interpreted cautiously; the effect size is therefore emphasised. No series in the sample carried a usable DLP record in the free-text dose comment attribute.

Availability is only half the question: a series with no recorded dose can still be surveyed if its acquisition is described completely enough for the reconstruction to run. Over the 400 the description was complete enough in 52.5%, from 17.0% (Philips) to 73.0% (GE and Canon/Toshiba). That marginal rate describes the archive; the reconstruction exists to answer the conditional one. **Of the 206 series that lacked a recorded CTDIvol, 84 (40.8%) retained enough acquisition metadata to reconstruct one**, and that rate is far more manufacturer-dependent than the marginal rate suggests (Table 1): the reconstruction closes the gap for GE, where 73 of 100 dose-less series are recoverable, and essentially none of it for Siemens, where none of 48 are.

**Table 1.** Recorded-dose availability and reconstructability by manufacturer, in 400 public CT series (100 per manufacturer).

| Manufacturer | CTDIvol absent | Of those, reconstructable | Conditional rate | Recoverable by either route |
| --- | ---: | ---: | ---: | ---: |
| GE | 100 / 100 (100.0%) | 73 | **73.0%** | 73 (73.0%) |
| Siemens | 48 / 100 (48.0%) | 0 | **0.0%** | 52 (52.0%) |
| Philips | 34 / 100 (34.0%) | 2 | **5.9%** | 68 (68.0%) |
| Canon/Toshiba | 24 / 100 (24.0%) | 9 | **37.5%** | 85 (85.0%) |
| **All** | **206 / 400 (51.5%)** | **84** | **40.8%** | **278 (69.5%)** |

There is a symmetry worth stating plainly: the manufacturer whose series never carry a recorded CTDIvol is also the one whose headers most often retain the technique needed to rebuild it. Taking both routes together, 278 of 400 series (69.5%) were dose-recoverable, leaving 122 (30.5%) that no method here can survey.

Figure 2B shows which attributes were present: tube current, slice thickness and slice location are near-universal, total collimation and pitch are not, and those two limit feasibility. Scanner model coverage was a separate constraint: of 400 series the table resolved 107 model names, found 49 ambiguous between admissible rows, and did not cover 244. The commonest uncovered names were `Aquilion` (64 series), `LightSpeed16` (15), `Discovery CT750 HD` (14) and `iCT 256` (13); the first is a family name identifying no single row, reported as unknown rather than assigned to a nearby model.

### 3.3 Layer 2b: accuracy of the physics reconstruction

Of the 194 screened series carrying a recorded CTDIvol, 124 were excluded because the scanner model was not in the coefficient table (108) or the series exceeded the 250-image download limit (45); 29 series fell into both categories and are counted once. Of the 70 remaining eligible series, a seeded random subsample capped at 14 per manufacturer (42 series) was retrieved in full; 4 lacked complete acquisition parameters, leaving 38 evaluable cases (Siemens 12, Canon/Toshiba 14, Philips 12). The cap was set by download budget, not by any statistical criterion. The 38 comprise 35 distinct (recorded, reconstructed) pairs, public archives re-releasing the same acquisition under different collection names.

Pooled over all 38 cases the median relative error was +33.5%, but that figure should not be quoted, for two reasons the stratification in Figure 3 makes explicit. First, one series' recorded CTDIvol (0.05 mGy) belongs to a localiser rather than the diagnostic acquisition reconstructed; comparing them compares different irradiations and produced a five-figure relative error. Restricting to diagnostic acquisitions (recorded CTDIvol of at least 0.5 mGy) leaves 37 cases.

Second, and more important, the error is governed by whether the header records the total collimation. The over-beaming factor grows roughly as $1/h_{col}$ once the collimation falls below the reference beam width, so a series omitting it — rebuilding it from table feed, or at worst from slice thickness — carries a much larger error: median +20.5% for the 24 cases with a recorded collimation and +156.4% for the 14 without. Both effects are confounded with manufacturer: Siemens contributed 12 with a recorded collimation and none without, Canon/Toshiba 12 with and 2 without, Philips none with and 12 without. Philips' apparently poor error is therefore an artefact of missing metadata rather than a failure of the dose model — and, equally, the reconstruction is **unvalidated for Philips on this archive**.

The stratum that isolates the dose model from the archive's metadata gaps is diagnostic acquisitions whose header records the collimation: n = 23, median relative error +18.9% (95% percentile-bootstrap CI +7.3% to +39.5%), interquartile range +3.5% to +39.5%, full range −56.3% to +107.2%, mean absolute error 34.9%, with 43.5% of cases within ±20% and 73.9% within ±50%; Canon/Toshiba (n = 12) had a median of +22.5% and Siemens (n = 11) +9.2%. All 23 are distinct (recorded, reconstructed) pairs — the duplicate re-releases lie outside this stratum — but they come from only 15 collections, so the interval may underestimate uncertainty to the extent that series from one collection are not independent.

A third effect is the tube voltage. Twenty-nine of the 38 cases were acquired at the voltage their coefficient row is tabulated at, where the correction of §2.3 is inert; their median error was +52.9%. For the other 9 the correction is what the reconstruction rests on: reported both ways from identical inputs, their median error was +54.5% uncorrected and +22.9% corrected. The improvement is not uniform, and the disaggregation matters more than the pooled figure: at 100 kV (n = 5) the median error fell from +80.1% to +25.1% and at 140 kV (n = 2) from −41.4% to −20.3%, but at 135 kV (n = 2), both Canon/Toshiba, it *rose*, from +3.7% to +31.2%. A squared voltage dependence is therefore useful here but not validated, and the two cases it degrades are as informative as the seven it improves: for at least one manufacturer the tabulated coefficient does not behave as a pure 120 kV reference. Both columns are released per case, and the bowtie inference resolved the row for 4 of the 38, otherwise reported as a median across candidates.

### 3.4 Size correction and effective dose on the surveyed series

The same 38 series were passed through the size-correction and effective-dose layers, so those components are evaluated on archive data rather than only described. Of the 38, one could not be contoured, its images being reconstructed at two matrix sizes within one series, leaving 37; we then excluded 2 scans of physical test objects (radiomics or QIBA phantom collections, whose water-equivalent diameter is valid but is not a patient size), 3 series of fewer than 10 slices and the localiser identified in §3.3, leaving **32 patient-like series**. Excluded series are retained in the released data with the flag that excluded them.

Across those 32 the measured water-equivalent diameter had a median of 31.5 cm (interquartile range 28.0–38.2 cm, full range 16.6–52.8 cm), giving TG-204 conversion factors with a median of 1.16 (IQR 0.91–1.32, range 0.71–2.01); SSDE from the recorded CTDIvol had a median of 11.6 mGy (IQR 9.3–22.7 mGy). Because SSDE is CTDIvol times that factor, the SSDE-to-CTDIvol ratio *is* the conversion factor, and its spread measures how far a phantom-referenced index departs from a size-corrected one across this cohort.

Effective dose could be computed for **29 of the 32**. The three exceptions are a finding in their own right: all their slices report an identical longitudinal position, so the scan range collapses to zero. SSDE is unaffected, having no longitudinal dependence, but DLP and anything derived from it are not recoverable. Across the 29, effective dose had a median of 13.1 mSv (IQR 4.8–21.5 mSv, range 0.6–30.9 mSv) — a population estimate for a reference phantom, not an individual patient's dose. These inherit the collimation dependence of §3.3, being derived from a recorded CTDIvol but a reconstructed scan length, so the Philips series carry the same inflation.

### 3.5 Size and organ-level demonstration

The size and organ layers are demonstrated on TCIA `LDCT-and-Projection-data` subject L273, an abdominal acquisition at 120 kVp over 165 images (§2.7). Tube current ranged 271–653 mA about a mean of 437.6 mA — a modulation depth (max − min) of 87.3% of the mean — over a 492 mm scan range (Figure 4).

The weighting ratio $\bar{I}_o/\bar{I}$ for all 17 segmented structures ranged from 0.645 to 1.480 (Figures 5 and 6): the organ-specific weighted CTDIvol of the urinary bladder here is 2.3 times that of the adrenal glands. Twelve of the seventeen fall below 0.72 and only the urinary bladder exceeds unity by more than 1%, which is what a single tube-current maximum near one end of the scan produces. That magnitude is a property of this examination's modulation, not a general statement; Khatonabadi et al. report the corresponding organ-to-global differences across a clinical cohort [10]. We do not label the high- and low-current portions anatomically, for the same reason the scan axis in Figure 4 is unlabelled.

### 3.6 Validation against a published per-series dose

Section 3.3 tests the reconstruction against the scanner's own recorded CTDIvol, the only reference a public archive usually offers. One collection offers a stronger one: *Low Dose CT Image and Projection Data* publishes a per-series CTDIvol and SSDE in a clinical data report while its **image headers carry neither**, so reference and test share no input — the images supply the acquisition description, the report the answer. We used the two subjects of §2.7; reference values are read from the published report at run time, matched on Series Instance UID.

Subject L277, at 100 kVp against a 120 kV coefficient row (factor 0.6944), tests the correction: CTDIvol 14.78 mGy uncorrected against a published 10.12 mGy (+46.0%) and 10.26 mGy corrected (**+1.4%**); from a measured water-equivalent diameter of 29.0 cm, SSDE 18.91 mGy uncorrected against a published 13.42 mGy (+40.9%) and 13.13 mGy corrected (**−2.1%**).

Subject L273, at the tabulated 120 kVp so the factor is exactly 1, tests the reconstruction alone: CTDIvol 16.29 mGy against a published 24.63 mGy (−33.9%), SSDE 20.55 mGy against a published 23.56 mGy (−12.7%). That residual is not a voltage effect and its source is identifiable — neither header records pitch or total collimation, so both are rebuilt from the table feed under an assumed pitch of 1. Inverting the reconstruction for the pitch that reproduces the published value gives 0.633 for L273, within the range routinely used for abdominal CT and implying a total collimation of 36.3 mm against the 23.0 mm the assumption yields, and 1.015 for L277, which is why the assumption costs L277 almost nothing. Together the cases show both ends on fully public data: adequately described, the reconstruction lands within a few percent of a published value; with the pitch unrecorded, a third of the index is lost to one assumption.

### 3.7 Data-quality findings, reported rather than corrected

Machine extraction and real-data analysis surfaced defects in the inputs. None was silently corrected; each is flagged in the shipped data, carries a regression test and is reported here.

In the reference implementation from which the coefficients were extracted: one head-mode free-in-air CTDI coefficient for a GE LightSpeed VCT configuration evaluates to roughly an order of magnitude above any plausible figure and contradicts its own inline comment; one TG-204 measured-dimension entry disagrees with the effective-diameter table at the same effective diameter, found by cross-checking the two table families; and the paediatric branches never assign the scanner correction factor, relying on a MATLAB global retaining the previous branch's value, which we resolved to the adult branch. In the archive data, at least one series carries a valid but physically nonsensical CTDIvol (a large negative binary value), excluded by a plausibility range and counted rather than averaged in.

One paediatric chest CTDIvol DRL literal reads 141 mGy, inconsistent with adjacent age bands. Tracing it to the primary source settled it: the nationwide survey underlying the J-RIME 2015 paediatric DRLs reports a 75th-percentile CTDIvol of 14.3 mGy and DLP of 296.0 mGy·cm for chest CT at 1–5 years on the 16 cm phantom [19], so the intended value is 14 mGy and 141 is a single-digit insertion. The shipped table carries the corrected value, the source literal and that citation, so the correction can be checked rather than taken. No paediatric DRL comparison is reported in this work.

## 4. Discussion

The principal finding is that recorded CT dose availability differed markedly among manufacturers in this TCIA sample, by up to 76 percentage points. Manufacturer is unlikely to be the whole explanation — scanner model, the contributing site's DICOM export configuration and each collection's curation and de-identification pipeline may all contribute, and this design cannot separate them — but the association is strong enough that any pooled statement that "public CT data lacks CTDIvol" is misleading in both directions, and a study sampling one collection or one scanner family will reach a rate that does not generalise. The second is that roughly three in ten series cannot be dose-surveyed by any method we implemented, which places a hard ceiling on retrospective dose research using such archives and is the more actionable figure for study planning.

Table 1 sharpens both: the reconstruction is not a uniform remedy, recovering 73.0% of GE's dose-less series and none of Siemens'. That the manufacturer with no recorded CTDIvol is also the one that most reliably retains the technique needed to rebuild it is fortunate rather than designed, and should not be generalised to other archives or to clinical PACS, where export may be configured differently.

This work complements established dose-management infrastructure rather than competing with it. Systems such as OpenREM harvest RDSR objects from clinical PACS at institutional scale [15], and where an RDSR exists our parser reads the same standard content items. What we add is the case where none exists: a provenance model keeping recorded and reconstructed values distinguishable, a physics reconstruction with a measured uncertainty, and size-specific and organ-level layers, all runnable on public data. The organ layer implements the organ-specific weighted CTDIvol of Khatonabadi et al. [10] and Tian et al. [11], stopping short of the Monte-Carlo normalisation those authors use to reach absorbed dose; coupling this pipeline to such coefficients [12] is the natural extension. The contribution is their combination in an implementation whose every value is auditable and whose validation is reproducible from public inputs.

The stratified error analysis carries a message for metadata practice as much as for dosimetry. A single pooled error would have attributed to the dose model an uncertainty caused mostly by absent metadata: the median error is +20.5% when the collimation is recorded and +156.4% when it must be rebuilt, and §3.6 shows one unrecorded pitch costing a third of the index against a published reference. The theme recurs in §3.4, where three series yielded no effective dose because every slice reported an identical longitudinal position. Retaining total collimation, pitch and slice position costs a de-identification pipeline nothing in privacy — none is an identifier — yet their loss forecloses dose analysis downstream. We have not costed the change, but the attributes are already in the source objects.

**Limitations.**

- The organ layer reports an organ-specific weighted CTDIvol, not an absorbed organ dose; converting it needs CTDIvol-normalised coefficients [11] we lack for these series. It is demonstrated on one series, and its spread of 0.645–1.480 reflects that acquisition's modulation rather than being general.
- The check of §3.6 rests on two subjects from one collection. It shows the reconstruction reaching a published value within a few percent when the acquisition is adequately described, and quantifies what one unrecorded parameter costs, but two cases support no distributional claim.
- The tube-voltage correction improved 7 of the 9 off-reference cases in §3.3 and degraded 2, both at 135 kV on one manufacturer; its scanner-specific validity is untested, and both columns are released per case. The bowtie is likewise inferred from the scan field of view against thresholds that are a pragmatic tripartition, not a vendor-published mapping, flagged in the 4 cases where it selected a row.
- Layer 1 rests on 14 objects from one sample collection — the largest openly redistributable multi-vendor RDSR set we could identify, but not a random sample of practice, and with no external ground truth.
- The accuracy arm is a capped subsample: 70 series were eligible and 42 retrieved, limited by download budget rather than any statistical criterion. It rests on 38 cases containing 35 distinct pairs and is unbalanced across manufacturers, so its interval may underestimate the true uncertainty.
- No GE series could enter the accuracy arm, none carrying a recorded CTDIvol to compare against, and no Philips series entered the complete-metadata stratum. The reconstruction is therefore *applicable* to GE — 73.0% of its dose-less series are reconstructable — but its *accuracy* on GE is unvalidated, and GE most needs it.
- The coefficient table covers 30 scanner models and did not cover 244 of 400 sampled series, so the reconstruction arm is not representative of the archive; the uncovered names are published in frequency order.
- The references in §3.3 and §3.6 are phantom-referenced indices, not absorbed patient dose, and §3.4 characterises the archive rather than validating anything: no independent measurement of water-equivalent diameter or organ dose was available.
- The figures used the full-resolution TotalSegmentator model; the fast model gives coarser organ boundaries, which matters less for a per-slice weighting than for volumetry but is not negligible.

**Future work.** The priorities are extending the coefficient table to the scanner families the archive contains, extending §3.6 to a whole collection that publishes per-series doses, validating against a collection carrying both RDSR objects and images, and calibrating the organ index against published Monte-Carlo coefficients.

## 5. Conclusions

An auditable dose pipeline makes CT dose surveillance possible on public imaging archives, and measuring one with it shows why such surveillance has been difficult: recorded dose indices were missing from roughly half of the public CT series we screened, at rates strongly associated with manufacturer, and around three in ten series carried neither a recorded index nor enough metadata to reconstruct one. Where reconstruction is possible its accuracy is governed chiefly by whether the total collimation survived de-identification, with a median error of +18.9% in the stratum where it did. The remedy is uneven across manufacturers, so both the availability and the accuracy of a reconstructed dose index must be reported per manufacturer rather than pooled. Carrying provenance with every value is what allows these figures to be reported honestly rather than collapsed into one misleading number.

---

## Declarations

**Conflicts of interest.** The author is the representative of LISIT Co., Ltd. (Tokyo, Japan) and Chief Executive Officer of TexelCraft OÜ, and this work was conducted under the supervision of LISIT Co., Ltd. The software is released under the MIT licence and the author has a commercial interest in downstream products that may use it. No patient or customer data were used.

**Funding.** None.

**Ethics approval and consent to participate.** Not applicable. This study used only publicly available, de-identified imaging data — TCIA collections released under Creative Commons licences [14, 18] — and third-party sample files. No new institutional review board approval and no informed consent were required.

**Data and code availability.** The software is available at https://github.com/Institute-of-One/ctdose-core under the MIT License. The version used in this study (version 0.1.0) is archived at Zenodo (https://doi.org/10.5281/zenodo.PLACEHOLDER-REPLACE-BEFORE-SUBMISSION). All numerical results reported here, including the rows they summarise, ship under `results/` with the scripts that generate them and the figure code.

**Imaging data are not redistributed.** Every image shown comes from a public TCIA collection and stays there, identified as described in §2.7 with retrieval instructions in `docs/REPRODUCING_FIGURES.md`. The reference doses of §3.6 are read at run time from the collection's published clinical data report, likewise not redistributed, and the RDSR sample objects remain with their GPL-3.0 distributor. No private, institutional or internally anonymised patient images were used at any point.

**Use of artificial intelligence.** TotalSegmentator (Apache-2.0) was used for inference only, in an optional component. A generative AI assistant (Claude, Anthropic) supported software development and drafted portions of this manuscript. All results were produced by executable code in the released repository and were re-run and verified by the author, who takes full responsibility for this work. The AI system is not an author and does not meet authorship criteria.

**Author contributions.** S.Y.: conceptualisation, methodology, software, validation, formal analysis, investigation, data curation, writing — original draft, writing — review and editing, visualisation.

---

## References

1. National Council on Radiation Protection and Measurements. *Medical Radiation Exposure of Patients in the United States*. NCRP Report No. 184. Bethesda, MD: NCRP; 2019.
2. American Association of Physicists in Medicine. *Size-Specific Dose Estimates (SSDE) in Pediatric and Adult Body CT Examinations*. AAPM Report No. 204. College Park, MD: AAPM; 2011.
3. American Association of Physicists in Medicine. *Use of Water Equivalent Diameter for Calculating Patient Size and Size-Specific Dose Estimates (SSDE) in CT*. AAPM Report No. 220. College Park, MD: AAPM; 2014.
4. American Association of Physicists in Medicine. *Estimating Patient Organ Dose with Computed Tomography: A Review of Present Methodology and Required DICOM Information*. AAPM Report No. 246. College Park, MD: AAPM; 2019.
5. American Association of Physicists in Medicine. *Size-Specific Dose Estimate for Head CT*. AAPM Report No. 293. College Park, MD: AAPM; 2019.
6. International Commission on Radiological Protection. *The 2007 Recommendations of the International Commission on Radiological Protection*. ICRP Publication 103. Ann ICRP. 2007;37(2–4).
7. National Electrical Manufacturers Association. *Digital Imaging and Communications in Medicine (DICOM) Standard, Part 16: Content Mapping Resource*. Templates TID 10011–10013, CT Radiation Dose. Rosslyn, VA: NEMA.
8. International Electrotechnical Commission. *Medical Electrical Equipment — Part 2-44: Particular Requirements for the Basic Safety and Essential Performance of X-ray Equipment for Computed Tomography*. IEC 60601-2-44. Geneva: IEC.
9. Stamm G, Nagel HD. CT-Expo — a novel program for dose evaluation in CT. *Rofo*. 2002;174(12):1570–1576. doi:10.1055/s-2002-35937
10. Khatonabadi M, Kim HJ, Lu P, et al. The feasibility of a regional CTDIvol to estimate organ dose from tube current modulated CT exams. *Med Phys*. 2013;40(5):051903. doi:10.1118/1.4798561
11. Tian X, Li X, Segars WP, Frush DP, Samei E. Prospective estimation of organ dose in CT under tube current modulation. *Med Phys*. 2015;42(4):1575–1585. doi:10.1118/1.4907955
12. Nuntue C, Matsubara K, Watanabe S, Fukushima K, Tantiwetchayanon K. Improved organ absorbed dose estimation in abdominal CT using DICOM header-based tube current modulation profiles: validation with measurements and Monte Carlo simulations. *J Appl Clin Med Phys*. 2025;26(11):e70321. doi:10.1002/acm2.70321
13. Wasserthal J, Breit H-C, Meyer MT, et al. TotalSegmentator: robust segmentation of 104 anatomic structures in CT images. *Radiol Artif Intell*. 2023;5(5):e230024. doi:10.1148/ryai.230024
14. Clark K, Vendt B, Smith K, et al. The Cancer Imaging Archive (TCIA): maintaining and operating a public information repository. *J Digit Imaging*. 2013;26(6):1045–1057. doi:10.1007/s10278-013-9622-7
15. OpenREM: open-source radiation exposure monitoring [software]. GPL-3.0. https://openrem.org (accessed 27 July 2026).
16. Japan Network for Research and Information on Medical Exposure (J-RIME). *Diagnostic Reference Levels Based on Latest Surveys in Japan — Japan DRLs 2015*. Tokyo: J-RIME; 2015.
17. Japan Network for Research and Information on Medical Exposure (J-RIME). *National Diagnostic Reference Levels in Japan — Japan DRLs 2020*. Tokyo: J-RIME; 2020.
18. Moen TR, Chen B, Holmes DR III, et al. Low-dose CT image and projection dataset. *Med Phys*. 2021;48(2):902–911. doi:10.1002/mp.14594
19. Takei Y, Miyazaki O, Matsubara K, et al. Nationwide survey of radiation exposure during pediatric computed tomography examinations and proposal of age-based diagnostic reference levels for Japan. *Pediatr Radiol*. 2016;46(2):280–285. doi:10.1007/s00247-015-3474-x

---

## Figure legends and figures

**Figure 1.** Vendor-neutral parsing of recorded dose from real CT Radiation Dose Structured Reports (Layer 1). **(A)** Per-irradiation-event CTDIvol from 14 objects spanning four manufacturers; marker shape is the CTDIw reference phantom the report states. Both phantoms occur, and a single study can mix them. **(B)** Internal consistency: the sum of the per-event DLPs against the accumulated DLP recorded independently in the same object, with the identity line shown as a dashed line. Agreement within 1% held for 14 of 14 comparable files.

![Figure 1](figures/fig1_layer1_rdsr_agreement.png)

**Figure 2.** Recorded-dose availability in 400 public CT series, 100 per manufacturer, sampled round-robin across 92 TCIA collections (Layer 2a). **(A)** Percentage of series with CTDIvol absent from the image header, with metadata complete enough for the reconstruction, and with the dose recoverable by either route. **(B)** Percentage carrying each acquisition attribute the reconstruction uses; total collimation and pitch are the limiting ones.

![Figure 2](figures/fig2_recorded_dose_availability.png)

**Figure 3.** Accuracy of the physics reconstruction against the scanner's recorded CTDIvol (Layer 2b, n = 38). **(A)** Reconstructed against recorded CTDIvol, with the identity line shown as a dashed line and factors of two as dotted lines. Circles are the headline stratum (diagnostic acquisitions with a recorded collimation), crosses the cases excluded from it. **(B)** Relative-error distribution by stratum, median marked. The pooled figure is dominated by cases whose collimation had to be reconstructed and by one localiser; the progression isolates the dose model from the archive's metadata gaps.

![Figure 3](figures/fig3_fallback_error.png)

**Figure 4.** Size and modulation analysis for the demonstration series: TCIA `LDCT-and-Projection-data` subject L273, "Full Dose Images", Siemens Healthcare SOMATOM Definition AS+, abdomen, 120 kVp, 165 images, tube-current modulated (CC BY 4.0 [18]; Series Instance UID in `docs/REPRODUCING_FIGURES.md`). Left: automatically extracted body contour on a mid-scan slice, excluding the table. Centre and right: slice-wise tube current and water-equivalent diameter over a coronal ray-sum. The axis is unlabelled anatomically because the sign of the slice-location axis depends on patient position and vendor convention.

![Figure 4](figures/fig4_modulation_and_wed.png)

**Figure 5.** TotalSegmentator organ masks (full-resolution model, inference only) on an abdominal slice of the same public series (subject L273), used to weight the slice-wise tube current. Seventeen dose-relevant structures were segmented; the legend lists those intersecting the displayed slice.

![Figure 5](figures/fig5_organ_overlay.png)

**Figure 6.** Tube-current weighting ratio $\bar{I}_o/\bar{I}$ for all 17 segmented structures of the same public series (subject L273); multiplying it by the global CTDIvol gives the organ-specific weighted CTDIvol of Khatonabadi et al. and Tian et al. [10, 11]. The dashed line marks unity, where an organ sees the scan-mean tube current. Values span 0.645–1.480, bounded by the modulation depth this acquisition applied ((max − min)/mean = 87.3%); that span is a property of this examination, not a general statement about how much organ dose can vary.

![Figure 6](figures/fig6_organ_exposure_index.png)
