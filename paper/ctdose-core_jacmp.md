# Open, reproducible, auditable CT dose surveillance on public data: a vendor-neutral RDSR/physics pipeline with size-specific and deep-learning organ dosimetry

**Shuji Yamamoto**

Institute of One, LISIT Co., Ltd., Tokyo, Japan

**Corresponding author:** Shuji Yamamoto, Institute of One, LISIT Co., Ltd., Tokyo, Japan. Email: yamamoto@lisit.jp

**Running title:** Auditable CT dose surveillance on public data

**Article type:** Research Article

**Section:** Diagnostic and Interventional Imaging Physics

**Keywords:** computed tomography; radiation dose; CTDIvol; size-specific dose estimate; radiation dose structured report; open-source software; reproducibility; diagnostic reference levels

---

## Abstract

**Purpose:** Retrospective CT dose surveillance on public imaging archives is limited by a problem that is rarely quantified: the recorded dose index is frequently absent from the image header. We built an open-source, fully auditable dose engine that reads a recorded dose where one exists and reconstructs it from acquisition physics where one does not, and we used it to measure how much of a public archive is actually surveyable.

**Methods:** `ctdose-core` (Python, MIT) computes CTDIvol, dose-length product (DLP), size-specific dose estimate (SSDE) and effective dose, returning every value as a provenance-carrying object that records its formula, its inputs and whether it was recorded or reconstructed. A vendor-neutral parser reads the CT Radiation Dose Structured Report (RDSR) using DICOM coded content items only. When no recorded dose exists, CTDIvol is rebuilt from tube current, rotation time, collimation and pitch using CT-Expo style normalised coefficients. SSDE follows AAPM TG-204 from an automatically contoured water-equivalent diameter, and an optional layer uses TotalSegmentator to obtain organ masks and weight them by the slice-wise tube-current modulation. Validation was in two layers: Layer 1 parsed 14 real RDSR objects from four manufacturers; Layer 2 screened 400 public CT series from The Cancer Imaging Archive (TCIA), 100 per manufacturer, sampled round-robin across 92 collections, and compared the reconstruction against the recorded CTDIvol wherever one was present.

**Results:** Layer 1 parsed 14 of 15 objects spanning 4 manufacturers, 9 scanner models and 67 irradiation events, with 44 events carrying a CTDIvol spanning 0.14–222.59 mGy across both the 16 cm and 32 cm reference phantoms. Two independent internal consistency checks passed on 14 of 14 files. In Layer 2, CTDIvol was absent from the image header in 51.5% of series overall but the rate was strongly manufacturer-dependent, ranging from 24.0% to 100.0%; no series carried a usable DLP record. The physics reconstruction was feasible for 52.5% of series, so 69.5% were recoverable by one route or the other. Against a recorded CTDIvol, and restricted to diagnostic acquisitions whose header records the total collimation (n = 23), the reconstruction had a median relative error of +18.9% (interquartile range −2.9% to +54.5%), with 39.1% within ±20% and 60.9% within ±50%. Error was dominated by whether collimation was recorded (median +20.5% when recorded, +156.4% when reconstructed) rather than by the dose model.

**Conclusions:** Recorded CT dose availability in a public archive is a property of the manufacturer, not of the archive, and roughly a third of series cannot be dose-surveyed by any method. A physics reconstruction closes part of that gap, with an accuracy governed chiefly by the completeness of the acquisition metadata. Reporting provenance for every value, rather than a single number, is what makes such a survey defensible.

---

## 1. Introduction

Computed tomography accounts for the largest share of medical exposure to ionising radiation, and dose optimisation programmes rest on being able to measure delivered dose reliably and at scale. Two quantities underpin routine surveillance: the volume computed tomography dose index (CTDIvol) and the dose-length product (DLP), both defined against a standard cylindrical phantom of 16 cm or 32 cm diameter. Because these indices describe the phantom rather than the patient, AAPM Task Group 204 introduced the size-specific dose estimate (SSDE), which corrects CTDIvol using the patient's water-equivalent diameter, and national bodies publish diagnostic reference levels (DRLs) against which a facility's median values are compared.

Modern scanners record the delivered indices in a DICOM Radiation Dose Structured Report (RDSR), and dose-management systems such as OpenREM harvest them at scale from clinical PACS. That infrastructure works well inside an institution. It does not transfer to the public imaging archives on which most reproducible imaging research is now built, for a reason that is simple and, to our knowledge, not quantified in the literature: de-identified public collections generally do not carry dose structured reports, and their image headers often omit CTDIvol as well. A study that wishes to relate image findings to delivered dose on public data therefore has no recorded dose to work from.

There is a physically principled way out. The acquisition description that public archives *do* retain — tube voltage, slice-wise tube current, rotation time, collimation, pitch and slice geometry — is sufficient to reconstruct CTDIvol from a scanner's normalised CTDI coefficients, the approach used by the CT-Expo methodology. That reconstruction is only as good as the metadata it is given, and its uncertainty has to be measured rather than assumed.

Two further requirements follow if such a reconstruction is to be used in research. First, a reconstructed dose index and a recorded one are not interchangeable, and a survey that mixes them silently is not defensible; the provenance of every value must travel with it. Second, the implementation must be open, because a dose figure that cannot be re-derived by a reader is not a measurement.

This work reports an open-source implementation meeting those requirements and the survey it made possible. We ask three questions. Can a vendor-neutral parser read recorded dose correctly from real RDSR objects across manufacturers? How much of a public CT archive carries a recorded dose, or enough metadata to reconstruct one, and does this depend on the manufacturer? And where a recorded value exists, how accurate is the reconstruction, and what governs its error?

## 2. Materials and methods

### 2.1 Open dose engine and provenance model

`ctdose-core` is a Python package (MIT licence) with a functional API and a command-line interface; it depends only on `pydicom`, `numpy` and `scipy`, and it has no graphical-interface dependency. Every quantity it returns is a `Quantity` object carrying the value, its unit, the formula that produced it, the inputs to that formula, a literature reference where one applies, and — crucially — a `Source` enumerating whether the value was read from an RDSR, read from a DICOM attribute, parsed from a free-text dose comment, reconstructed from physics, or derived from other quantities. Downstream consumers can therefore separate recorded from reconstructed values without re-tracing the computation, and a survey can report the split.

Where a value could come from more than one place, the engine applies an explicit ranking:

> RDSR > image header attribute (0018,9345) > free-text dose comment (0040,0310) > physics reconstruction.

Lower-ranked values are not discarded. They are retained alongside the winner and their difference is reported, because a disagreement between an RDSR and an image-header value usually indicates that the wrong irradiation event was attributed to the series rather than a genuine dose discrepancy.

### 2.2 Vendor-neutral RDSR parsing

The RDSR parser is written against the coded content items of DICOM PS3.16 templates TID 10011–10013 — the `DCM` coding scheme and its documented code values — rather than against any vendor's layout. It walks the SR content tree and extracts, per irradiation event, the mean CTDIvol (code 113830), the DLP (113838), the CTDIw reference phantom (113835) and the acquisition technique, together with the study-level accumulated DLP (113813) and the declared number of irradiation events (113812).

An RDSR describes a whole examination, not a single reconstructed series, so attributing an event to a series is an explicit step. The parser matches on acquisition protocol name first and on scanning length second. When no event can be attributed unambiguously, no recorded value is taken from the report and the pipeline falls back to the image header or to physics; attaching another event's CTDIvol to a series is a worse error than declining to attribute one.

A CTDIvol read from an RDSR carries its own reference phantom, which then overrides the body-region heuristic when the SSDE conversion table is selected.

### 2.3 Physics reconstruction when no recorded dose exists

When no recorded CTDIvol is available, it is reconstructed following the CT-Expo formulation:

$$k_{OB} = \frac{N\,(h_{col} + d_z)}{h_{col}\,(N + d_z)}, \qquad
L_{rot} = \frac{h_{rec}}{2} + h_{col}\,(m_{OR}\,p + b_{OR}),$$

$$\mathrm{CTDI_{vol}} = n\mathrm{CTDI_w} \cdot (I \cdot t_{rot}) \cdot \frac{k_{OB}}{p}, \qquad
\mathrm{DLP} = \mathrm{CTDI_{vol}} \cdot \frac{L_{scan} + L_{rot}}{10},$$

where $I$ is the mean tube current over the series, $t_{rot}$ the rotation time, $h_{col}$ the total collimation, $p$ the pitch, $h_{rec}$ the reconstructed slice thickness and $L_{scan}$ the reconstructed scan range in mm. The over-beaming factor $k_{OB}$ corrects for the penumbra outside the nominal beam relative to the reference beam width $N$ at which the coefficient was measured, and $L_{rot}$ accounts for helical over-ranging. Effective dose is obtained either from the free-in-air DLP and an organ-conversion factor, or from DLP and an ICRP Publication 103 $k$-factor, according to the route the scanner's coefficient row prescribes; both routes carry the tube-voltage corrections $(kV/kV_{ref})^2$ and $(kV/kV_{ref})^{0.5}$.

The coefficients $n\mathrm{CTDI_w}$, $n\mathrm{CTDI_{air}}$, $N$, $d_z$, $m_{OR}$, $b_{OR}$ and $kV_{ref}$ are specific to the scanner series, body region and patient size. They were machine-extracted from a previously validated MATLAB reference implementation rather than transcribed by hand, yielding 149 usable rows covering 30 scanner models from four manufacturers. The extraction scripts ship with the package, and the port is verified against an independent literal transcription of the reference expressions to a relative tolerance of $10^{-12}$ across every row.

Matching a DICOM `ManufacturerModelName` to a coefficient row is a one-to-many problem — the header does not record the bowtie filter, scan field of view or acquisition mode that the coefficients distinguish. Matching is by token overlap, never crosses manufacturers, and rejects weak matches: a family name that does not identify a row is reported as unknown rather than costed against a nearby model. Where several rows remain admissible, the median estimate across candidates is reported and every candidate is retained.

### 2.4 Size-specific dose estimate

The water-equivalent diameter is computed from an automatically extracted body contour as $D_w = 2\sqrt{(\overline{\mathrm{HU}}/1000 + 1)\,A_{ROI}/\pi}$, where $\overline{\mathrm{HU}}$ is the mean Hounsfield value inside the contour and $A_{ROI}$ its area. The contour uses a fixed attenuation threshold, largest-connected-component selection and hole filling, so it requires no deep-learning dependency and is deterministic across platforms; the table and any detached support hardware are excluded, and interior gas is retained inside the region of interest as the definition requires. SSDE is then $\mathrm{CTDI_{vol}} \cdot f(D_w)$ with $f$ interpolated from the TG-204 conversion tables for the applicable 16 cm or 32 cm phantom, and the closed-form head conversion $f = 1.9852\exp(-0.0486 D_w)$ where that applies. The implementation is verified against synthetic phantoms with an analytically known answer: a uniform 0 HU disc of radius $r$ must return $D_w = 2r$ end-to-end through the segmentation, and doubling the attenuation must scale it by exactly $\sqrt{2}$.

### 2.5 Deep-learning organ layer

An optional extra uses TotalSegmentator (Apache-2.0), for inference only, to obtain organ masks. Given the slice-wise tube current $I(z)$ from (0018,1151) and an organ occupying slices $Z_o$ with per-slice voxel counts $n_o(z)$, we form the organ's voxel-weighted mean tube current and divide it by the scan mean:

$$\bar{I}_o = \frac{\sum_{z \in Z_o} n_o(z)\,I(z)}{\sum_{z \in Z_o} n_o(z)}, \qquad \bar{I} = \frac{1}{|Z|}\sum_{z \in Z} I(z), \qquad w_o = \frac{\bar{I}_o}{\bar{I}}.$$

$w_o$ is the organ's tube-current-weighted exposure relative to the scan mean; the organ's relative dose index is $\mathrm{CTDI_{vol}} \cdot w_o$. This is an exposure-weighting model, **not** a Monte-Carlo organ dose. Converting $w_o$ to absolute organ dose requires scanner- and phantom-specific normalisation coefficients from a published Monte-Carlo or measurement study, and the software applies them only when the caller supplies both the coefficients and their citation. The core package never imports TotalSegmentator; without the extra, patient size falls back to the threshold-based contour and everything except organ segmentation works unchanged.

### 2.6 Auditable DRL comparison

Measured indices are compared against diagnostic reference levels, and each comparison emits an alert that states the metric, the reference set and examination, the numeric excess, the ratio, the patient size band and the provenance of the measured value. No classifier score is produced. Because a DRL applies to the median of a representative sample rather than to an individual examination, that caveat is attached to every alert. Additional DRL sets can be registered at runtime.

### 2.7 Data

**Layer 1 (recorded-dose parsing).** The RDSR validation set is the CT dose structured report sample collection distributed with OpenREM (GPL-3.0), comprising 15 objects from four manufacturers. These are third-party files and are not redistributed with our software; the tests read them from a user-supplied directory and skip cleanly when it is absent, and only the measurements read out of them appear in our results.

**Layer 2 (public-archive survey).** CT series were drawn from The Cancer Imaging Archive through its public NBIA REST interface, with no credentials and no write access. Series were selected by DICOM `Manufacturer` and sampled round-robin across each manufacturer's collections so that no single large collection could dominate the rate; the sampling seed, sample sizes and access timestamps are recorded in the released results. Each series is identified in the released data by its Series Instance UID together with the licence the archive states for it; the licences encountered were Creative Commons Attribution 3.0 and 4.0 and their non-commercial variants, and 16 of 400 series carried no licence label in the archive index. All collections used are publicly available and de-identified, so no new institutional review board approval and no informed consent were required.

**Demonstration series.** The images used for the modulation, contour and organ figures are a dummy-patient phantom acquisition (`DOSE TARO`, GE LightSpeed VCT, 120 kV, chest–pelvis, 147 slices) that contains no protected health information. A scrubbed 12-slice subset ships with the software as a regression fixture.

### 2.8 Reproducibility

Every number reported here is written by a script into machine-readable result files that ship with the software, and the manuscript quotes those files rather than restating computed values. An automated test re-derives each quoted figure from the result files and fails if the manuscript and the data disagree, and a second test recomputes every summary statistic from the per-case rows it claims to summarise, so neither a stale result file nor a hand-edited figure in the text can pass continuous integration. Figures are likewise generated from the same files by a script in the repository rather than drawn by hand.

## 3. Results

### 3.1 Layer 1: recorded dose is read correctly across manufacturers

Of 15 sample objects, 14 parsed as CT Radiation Dose Structured Reports; the fifteenth is a secondary-capture dose screen and was correctly rejected. The 14 objects span 4 manufacturers (GE, Siemens, Philips, Canon/Toshiba) and 9 scanner models, and contain 67 irradiation events, of which 44 carry a CTDIvol and 44 a DLP. Recorded CTDIvol spans 0.14–222.59 mGy and DLP 1.2–890.26 mGy·cm. Both the 16 cm head and the 32 cm body reference phantoms are represented, and a single study can mix them (Figure 1A).

Two internal consistency checks were computed per file and reported rather than assumed. The sum of the per-event DLPs agreed with the study-level accumulated DLP recorded in the same object within 1% in 14 of 14 comparable files (Figure 1B), and the number of irradiation events parsed equalled the number the report itself declares in 14 of 14 files. In addition, a template-blind sweep of the entire SR content tree — collecting every occurrence of the relevant code values regardless of where in the tree they sit — returned exactly the values the template-navigating parser returned, for CTDIvol, DLP, accumulated DLP and phantom code, in every file. That sweep is an independent oracle: it agrees with the parser only if the parser has neither dropped a branch nor read a value from the wrong container. It also identified one branch the parser had initially been skipping, the phantom code attached to the accumulated DLP rather than to an event, which was then added.

### 3.2 Layer 2a: recorded dose availability in a public archive

Four hundred CT series were screened, 100 per manufacturer, drawn from 92 collections. CTDIvol was absent from the image header in 194 of 400 series overall (51.5%), but the pooled figure conceals the dominant effect (Figure 2A). The absence rate was 100.0% for GE, 48.0% for Siemens, 34.0% for Philips and 24.0% for Canon/Toshiba: within the same archive, whether a recorded dose index survives into a public collection is essentially a property of the manufacturer. No series in the sample carried a usable DLP record in the free-text dose comment attribute.

Availability of a recorded dose is only half the question, because a series with no recorded dose can still be surveyed if its acquisition is described completely enough for the reconstruction to run. That was true for 52.5% of series overall, and again varied by manufacturer: 73.0% for both GE and Canon/Toshiba, 47.0% for Siemens and 17.0% for Philips. Taking the two routes together, 69.5% of series were recoverable by one or the other — 85.0% for Canon/Toshiba, 73.0% for GE, 68.0% for Philips and 52.0% for Siemens. The complement is the fraction of the archive that no method can dose-survey.

Figure 2B shows which acquisition attributes were present. Tube current, slice thickness and slice location are near-universal; total collimation and pitch are not, and it is those two that limit feasibility. Scanner model coverage was a separate constraint: of 400 series, the coefficient table resolved 107 model names, found 49 ambiguous between admissible rows, and did not cover 244. The most frequent uncovered names were `Aquilion` (64 series), `LightSpeed16` (15), `Discovery CT750 HD` (14) and `iCT 256` (13); the first of these is a manufacturer family name that does not identify a single coefficient row, and was deliberately reported as unknown rather than assigned to a nearby model.

### 3.3 Layer 2b: accuracy of the physics reconstruction

Among the screened series carrying a recorded CTDIvol, 38 could be evaluated after excluding those whose scanner model was not in the coefficient table (108), whose series exceeded the download size limit (45), and whose acquisition parameters were incomplete (4). The 38 cases comprise 35 distinct (recorded, reconstructed) pairs, because public archives re-release the same acquisition under different collection names; the effective sample is the distinct count.

Pooled over all 38 cases the median relative error was +53.2%, but that figure should not be quoted, for two reasons that the stratification in Figure 3 makes explicit.

First, one series' recorded CTDIvol (0.05 mGy) belongs to a localiser rather than to the diagnostic acquisition that was reconstructed; comparing the two compares different irradiations and produced a five-figure relative error. Restricting to diagnostic acquisitions (recorded CTDIvol ≥ 0.5 mGy) leaves 37 cases.

Second, and more important, the error is governed by whether the header records the total collimation. The over-beaming factor grows roughly as $1/h_{col}$ once the collimation falls below the reference beam width, so a series whose header omits it — leaving the value to be rebuilt from table feed, or in the worst case from slice thickness — carries a much larger error than one that records it. Median error was +20.5% for the 24 cases with a recorded collimation and +156.4% for the 14 without.

These two effects are confounded with manufacturer, because collimation availability is itself manufacturer-dependent: of the evaluable cases, Siemens contributed 12 with a recorded collimation and none without, Canon/Toshiba 12 with and 2 without, and Philips none with and 12 without. Philips' apparently poor per-manufacturer error is therefore an artefact of missing metadata rather than a manufacturer-specific failure of the dose model — and, equally, the reconstruction is **unvalidated for Philips on this archive**.

The stratum that isolates the dose model from the archive's metadata gaps is diagnostic acquisitions whose header records the collimation: n = 23, median relative error +18.9%, interquartile range −2.9% to +54.5%, full range −57.8% to +198.3%, mean absolute error 45.4%, with 39.1% of cases within ±20% and 60.9% within ±50%. Within that stratum, Canon/Toshiba (n = 12) had a median error of +17.0% and Siemens (n = 11) +53.5%.

### 3.4 Size and organ-level demonstration

Figure 4 shows the size and modulation analysis for the demonstration series: the automatically extracted body contour on a mid-scan slice, and the slice-wise tube current and water-equivalent diameter overlaid on a coronal ray-sum. Tube current on this acquisition ranged 119–143 mA about a mean of 127.4 mA, a modulation depth of 18.8% of the mean, and the water-equivalent diameter tracked patient thickness along the scan.

Figure 5 shows the TotalSegmentator organ masks for the same series, and Figure 6 the resulting exposure index $w_o$ for all 17 segmented structures. Values ranged from 0.934 to 1.121. That range is narrow because it is bounded by the modulation the scanner actually applied on this acquisition; the figure demonstrates that the weighting is computed correctly and shows which structures sat in the higher- and lower-current portions of the scan, but it is not evidence about how much organ exposure can differ in general. Many structures share an identical value because they lie within a long plateau of the tube-current profile.

### 3.5 Data-quality findings, reported rather than corrected

Machine extraction and real-data analysis surfaced defects in the inputs. None was silently corrected; each is flagged in the shipped data, carries a regression test, and is reported here.

In the reference implementation from which the coefficients were extracted: one head-mode free-in-air CTDI coefficient for a GE LightSpeed VCT configuration evaluates to a physically implausible value roughly an order of magnitude above any plausible figure and contradicts its own inline comment; one entry of a TG-204 measured-dimension table disagrees with the effective-diameter table at the same effective diameter, detected automatically by cross-checking the two table families against each other; and the paediatric branches never assign the scanner correction factor, relying on a MATLAB global retaining the previous branch's value, which we resolved explicitly to the adult branch of the same scanner and region and recorded as such.

One paediatric chest CTDIvol DRL literal in the reference implementation reads 141 mGy, which is inconsistent with the same examination's values in the adjacent age bands. **We have not been able to verify the published J-RIME value, and we therefore make no claim about it.** The shipped table carries both the source literal and a flag marking the row as requiring verification, and that row is excluded from any analysis in this work. No paediatric DRL comparison is reported here.

In the archive data: at least one series carries a syntactically valid but physically nonsensical CTDIvol (a large negative binary value), which is excluded from the error statistics by a plausibility range and counted rather than averaged in.

## 4. Discussion

The principal finding is that recorded CT dose availability in a public archive is a property of the manufacturer rather than of the archive, differing by up to 76 percentage points between manufacturers within the same collection set. Any single pooled statement that "public CT data lacks CTDIvol" is therefore misleading in both directions, and a study that samples one collection or one scanner family will reach a rate that does not generalise. The second finding is that roughly three in ten series cannot be dose-surveyed by any method we implemented — neither a recorded value nor a reconstructable one — which places a hard ceiling on retrospective dose research using such archives and is, we think, the more actionable number for people planning such studies.

Our work is complementary to established dose-management infrastructure rather than competing with it. Systems such as OpenREM are designed to harvest RDSR objects from clinical PACS at institutional scale, and where an RDSR exists our parser reads the same standard content items. What we add is the case where no such object exists: an explicit provenance model that keeps recorded and reconstructed values distinguishable, a physics reconstruction with a measured uncertainty, and an integration of size-specific and deep-learning organ dosimetry — all runnable on public data by a reader with no access to a clinical PACS. Similarly, automated SSDE and deep-learning size estimation have been reported before, and tube-current-modulation-weighted organ dose estimation is an active area; the contribution here is not any single one of these components but their combination in an implementation whose every value is auditable and whose validation is fully reproducible from public inputs.

The stratified error analysis carries a message for metadata practice as much as for dosimetry. Reporting a single pooled error for the reconstruction would have attributed to the dose model an uncertainty that is mostly caused by absent acquisition metadata: the median error is +20.5% when the collimation is recorded and +156.4% when it must be rebuilt. De-identification pipelines that strip or fail to carry total collimation and pitch impose a large, avoidable penalty on any downstream dose analysis, and the fix is inexpensive.

**Limitations.** The organ layer produces a relative exposure index, not an absolute Monte-Carlo organ dose; converting it requires published normalisation coefficients, which the software will apply only with a citation supplied by the caller. The demonstration series has shallow tube-current modulation, so the observed spread of the organ index is narrow and should not be read as a general result. Layer 1 rests on 14 objects from one publicly available sample collection, which is the largest openly redistributable multi-vendor RDSR set we could identify but is not a random sample of clinical practice; the paired per-event and accumulated dose values inside each object give internal validation, but there is no external ground truth. Layer 2's fallback comparison rests on 38 cases with 35 distinct pairs, is unbalanced across manufacturers, and includes no GE case at all because no sampled GE series carried a recorded CTDIvol — the reconstruction is therefore unvalidated for the very manufacturer whose data needs it most. The coefficient table covers 30 scanner models and did not cover 244 of 400 sampled series, so the survey's reconstruction arm is not representative of the whole archive; extending the table is the obvious next step and the uncovered model names are published in frequency order to direct it. We compared reconstruction against the scanner's own recorded CTDIvol, which is itself a phantom index and not a measurement of delivered patient dose. Finally, the segmentation figures used the full-resolution TotalSegmentator model; the fast model gives coarser boundaries at organ interfaces, which matters for a per-slice weighting less than it would for volumetry but is not negligible.

**Future work.** The clear priorities are extending the coefficient table to the scanner families the archive actually contains, validating the reconstruction on a collection that carries both RDSR objects and images so that recorded and reconstructed values can be compared against a per-event ground truth, and calibrating the organ index against published Monte-Carlo coefficients for matched protocols.

## 5. Conclusions

An open, auditable dose pipeline makes CT dose surveillance possible on public imaging archives, and measuring the archive with it shows why such surveillance has been difficult: recorded dose indices are missing from roughly half of public CT series, at rates that depend overwhelmingly on the manufacturer, and around three in ten series carry neither a recorded dose nor enough acquisition metadata to reconstruct one. Where a reconstruction is possible its accuracy is governed chiefly by whether the total collimation survived de-identification, with a median error of +18.9% in the stratum where it did. Carrying provenance with every value — recorded versus reconstructed, and by which route — is what allows these figures to be reported honestly rather than pooled into a single misleading number.

---

## Declarations

**Conflicts of interest.** The author is the representative of LISIT Co., Ltd. (Tokyo, Japan) and Chief Executive Officer of TexelCraft OÜ. This work was conducted under the supervision of LISIT Co., Ltd. The software described here is released under the MIT licence and the author has a commercial interest in downstream products that may use it. No patient data and no customer data were used in this work.

**Funding.** None.

**Ethics approval and consent to participate.** Not applicable. This study used only publicly available, de-identified imaging data and third-party sample files. No new institutional review board approval and no informed consent were required.

**Data and code availability.** The software is available at https://github.com/Institute-of-One/ctdose-core under the MIT licence, and an archived release with a citable DOI is deposited at Zenodo (DOI to be assigned at acceptance). All numerical results reported here, including the per-series and per-case rows they summarise, ship with the software under `results/`, together with the scripts that generate them and the figure-generation code. Imaging data are not redistributed: TCIA series are identified by Series Instance UID with the licence recorded per series, and the RDSR sample objects remain with their GPL-3.0 distributor.

**Use of artificial intelligence.** TotalSegmentator (Apache-2.0) was used for inference only, in an optional component. A generative AI assistant (Claude, Anthropic) was used to support software development and to draft portions of this manuscript. All results were produced by executable code in the released repository, were re-run and verified by the author, and the author takes full responsibility for the content and accuracy of this work. The AI system is not an author and does not meet authorship criteria.

**Author contributions.** S.Y.: conceptualisation, methodology, software, validation, formal analysis, investigation, data curation, writing — original draft, writing — review and editing, visualisation.

---

## References

*Volume, issue, page and DOI details must be verified against the publisher of record before submission.*

1. American Association of Physicists in Medicine. *Size-Specific Dose Estimates (SSDE) in Pediatric and Adult Body CT Examinations*. AAPM Report No. 204. College Park, MD: AAPM; 2011.
2. American Association of Physicists in Medicine. *Estimating Patient Organ Dose with Computed Tomography: A Review of Present Methodology and Required DICOM Information*. AAPM Report No. 246. College Park, MD: AAPM; 2019.
3. American Association of Physicists in Medicine. *Size-Specific Dose Estimate for Head CT*. AAPM Report No. 293. College Park, MD: AAPM; 2019.
4. Wasserthal J, Breit H-C, Meyer MT, et al. TotalSegmentator: robust segmentation of 104 anatomic structures in CT images. *Radiol Artif Intell*. 2023;5(5):e230024.
5. National Electrical Manufacturers Association. *Digital Imaging and Communications in Medicine (DICOM) Standard, Part 16: Content Mapping Resource*. Templates TID 10011–10013, CT Radiation Dose. Rosslyn, VA: NEMA.
6. International Commission on Radiological Protection. *The 2007 Recommendations of the International Commission on Radiological Protection*. ICRP Publication 103. Ann ICRP. 2007;37(2–4).
7. Stamm G, Nagel HD. CT-Expo — a novel program for dose evaluation in CT. *Rofo*. 2002;174(12):1570–1576.
8. International Electrotechnical Commission. *Medical Electrical Equipment — Part 2-44: Particular Requirements for the Basic Safety and Essential Performance of X-ray Equipment for Computed Tomography*. IEC 60601-2-44. Geneva: IEC.
9. Japan Network for Research and Information on Medical Exposure (J-RIME). *Diagnostic Reference Levels Based on Latest Surveys in Japan — Japan DRLs 2015*. Tokyo: J-RIME; 2015.
10. Japan Network for Research and Information on Medical Exposure (J-RIME). *National Diagnostic Reference Levels in Japan — Japan DRLs 2020*. Tokyo: J-RIME; 2020.
11. Clark K, Vendt B, Smith K, et al. The Cancer Imaging Archive (TCIA): maintaining and operating a public information repository. *J Digit Imaging*. 2013;26(6):1045–1057.
12. Charnock P. OpenREM: an open-source patient dose management system. *Radiat Prot Dosimetry*. 2015;165(1–4):484–487.

---

## Figure legends and figures

*Figures are supplied separately at 300 dpi; they are reproduced here for review.*

**Figure 1.** Vendor-neutral parsing of recorded dose from real CT Radiation Dose Structured Reports (Layer 1). **(A)** Per-irradiation-event CTDIvol read from 14 objects spanning four manufacturers, with marker shape indicating the CTDIw reference phantom the report states. Both the 16 cm head and 32 cm body phantoms occur, and a single study can mix them. **(B)** Internal consistency: the sum of the per-event DLPs plotted against the accumulated DLP recorded independently in the same object; the dashed line is identity. Agreement within 1% held for 14 of 14 comparable files.

![Figure 1](figures/fig1_layer1_rdsr_agreement.png)

**Figure 2.** Recorded-dose availability in 400 public CT series, 100 per manufacturer, sampled round-robin across 92 TCIA collections (Layer 2a). **(A)** Percentage of series with CTDIvol absent from the image header, with the acquisition metadata complete enough for the physics reconstruction, and with the dose recoverable by either route. **(B)** Percentage of series carrying each acquisition attribute the reconstruction uses. Total collimation and pitch are the limiting attributes.

![Figure 2](figures/fig2_recorded_dose_availability.png)

**Figure 3.** Accuracy of the physics reconstruction against the scanner's recorded CTDIvol (Layer 2b, n = 38). **(A)** Reconstructed against recorded CTDIvol; the dashed line is identity and the dotted lines are factors of two. Circles are the headline stratum (diagnostic acquisitions with a recorded collimation); crosses are cases excluded from it. **(B)** Relative-error distribution by stratum, with the median marked. The pooled figure is dominated by cases whose collimation had to be reconstructed and by one localiser; the bottom-to-top progression isolates the dose model from the archive's metadata gaps.

![Figure 3](figures/fig3_fallback_error.png)

**Figure 4.** Size and modulation analysis for the demonstration series (GE LightSpeed VCT, 120 kV, chest–pelvis, 147 slices). Left: automatically extracted body contour on a mid-scan slice, excluding the table. Centre and right: slice-wise tube current and water-equivalent diameter overlaid on a coronal ray-sum image. The axis direction is left unlabelled with respect to anatomy because the sign of the slice-location axis depends on patient position and vendor convention.

![Figure 4](figures/fig4_modulation_and_wed.png)

**Figure 5.** TotalSegmentator organ masks (full-resolution model, inference only) overlaid on an abdominal slice of the demonstration series, used to weight the slice-wise tube current.

![Figure 5](figures/fig5_organ_overlay.png)

**Figure 6.** Tube-current-weighted exposure index $w_o$ for all 17 segmented structures of the demonstration series. The dashed line marks $w_o = 1$, where an organ sees the scan-mean tube current. The range is narrow (0.934–1.121) because it is bounded by the modulation depth this acquisition actually applied (18.8% of the mean), and structures lying within a plateau of the tube-current profile share an identical value.

![Figure 6](figures/fig6_organ_exposure_index.png)
