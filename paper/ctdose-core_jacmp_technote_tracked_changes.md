# Tracked changes — Revision 1

**Manuscript:** An open-source, provenance-aware framework for reconstructing missing CT
dose indices from DICOM metadata. **Article type:** Technical Note (changed from Research
Article at the Section Editor's request).

This file compares the previously submitted masked manuscript with the masked Technical
Note submitted now. ~~Struck-through text~~ was removed from the main text; ++underlined
text++ is new. Text with no marking is unchanged.

The condensation is a relocation, not a re-analysis: no number changed, and the material
removed here is reproduced verbatim in the Supplementary Material file and in the public
repository. Both documents are anonymous for double-masked review.

---

# An open-source, provenance-aware framework for reconstructing missing CT dose indices from DICOM metadata

**Running title:** Provenance-aware CT dose reconstruction

**Article type:** Technical Note

**Section:** Diagnostic and Interventional Imaging Physics

**Keywords:** computed tomography; CTDIvol; size-specific dose estimate; radiation dose structured report; open data; reproducibility

---

## Abstract

**Background:** The recorded volume CT dose index (CTDIvol) is often absent from de-identified image headers, and public archives carry no radiation dose structured report (RDSR), leaving retrospective dose work no recorded value to use.

**Purpose:** To present an open-source, provenance-aware framework that recovers dose indices from the metadata surviving de-identification, keeps a recovered value distinguishable from a recorded one, and measures the limits of that recovery.

Methods: An open-source Python package (MIT License) computes CTDIvol, dose-length ~~product,~~ ++product (DLP),++ size-specific dose estimate and effective ~~dose, tagging every~~ ++dose. Every++ value ~~with~~ ++carries++ its formula, ++that formula's++ inputs, ++the++ assumptions ++they rested on,++ and a ~~source marking~~ ++label stating whether++ it ~~recorded, reconstructed, derived~~ ++was read from the data++ or ~~unrecoverable.~~ ++rebuilt from it.++ Where no recorded index survives, CTDIvol is rebuilt from tube current, rotation time, collimation and pitch ~~against~~ ++using++ an openly licensed table of ~~measured normalised~~ ++normalized++ CTDI values. ~~We evaluated~~ ++I parsed++ 14 RDSR ~~objects and~~ ++objects, screened++ 400 public CT ~~series.~~ ++series, and compared the reconstruction against two references — 46 archive series retaining a CTDIvol, and 5 GE irradiation events recording one — both console figures, and so not independent of that table.++

Results: All 14 RDSR objects parsed, ~~two~~ ++and both++ consistency checks ~~passing on~~ ++held in++ 14 of 14 files. CTDIvol was absent from the image header in 206 of 400 series (51.5%), ++ranging++ from 24.0% to 100.0% by manufacturer; of those 206, 42 (20.4%) ~~were reconstructable,~~ ++could still be reconstructed,++ making 236 of 400 (59.0%) dose-recoverable. Against ++the archive's own++ recorded values (n = 46) the median relative error was −8.7%, and −5.7% (95% CI −12.4% to +7.8%) where the header recorded the total ~~collimation.~~ ++collimation; 18 of the 46 were over-estimates and 28 under-estimates, so the error is two-sided rather than conservative. On GE, which supplies no archive reference, five events from the scanners' own dose reports were compared: their exposure time refutes the collimation four of them record, leaving that width undetermined and the error spanning −18.2% to +22.4%; the fifth, whose recorded geometry it corroborates, spans −0.0% to +12.7% over the unrecorded bowtie filter alone.++

Conclusion: Dose indices can be recovered when no recorded value survives, but only as far as the metadata and ~~available~~ ++the++ open ~~measurements~~ ++coefficient table++ allow, and provenance keeps a recovered value from being read as a recorded one.

---

## 1. Introduction

Computed tomography is the largest single contributor to collective medical exposure in most surveyed countries [1], and ~~optimisation~~ ++optimization++ rests on ~~measuring~~ CTDIvol and the dose-length product (DLP), phantom-referenced indices of scanner output [2]. Modern scanners record them in a DICOM Radiation Dose Structured Report (RDSR) [3], which dose-management systems such as OpenREM harvest from clinical PACS [4]. That infrastructure does not transfer to public imaging archives: de-identified collections generally carry no RDSR and often omit CTDIvol from the header ~~as well,~~ ++too,++ so a study relating image findings to the doses that produced them has no recorded value to work from. AAPM Report 246 catalogues the DICOM attributes organ dose estimation requires [5], but how often they survive de-identification is, to ~~our~~ ++my++ knowledge, unreported.

Where the recorded index is missing, the surviving acquisition description suffices to reconstruct CTDIvol from a scanner's ~~normalised~~ ++normalized++ CTDI coefficients, as dose-evaluation software has done since CT-Expo [6]. Such a reconstruction is only as ~~good~~ ++accurate++ as its metadata and its coefficients, so its uncertainty must be measured rather than ~~assumed~~ ++assumed,++ and both code and coefficients must be open. This note describes a framework meeting those conditions and measures how far it reaches.

## 2. Materials and methods

### [added] 2.1 Dose engine and the provenance labels

The package has a functional API and a command-line interface, and depends only on `pydicom`, `numpy` and `scipy`. Every quantity it returns carries four ~~provenance~~ levels ~~— the~~ ++of provenance:++ value and ~~its unit,~~ ++unit;++ the ~~formula,~~ ++formula;++ that formula's inputs and any assumption they rested ~~on,~~ ++on;++ and a ~~`Source` marking it recorded, reconstructed, derived or unrecoverable. Where a value could come from more than one place a ranking applies (RDSR > image header (0018,9345) > dose comment (0040,0310) > reconstruction) and the losers are kept~~ ++source label, used throughout++ with ~~their difference reported.~~ ++these meanings:++

++- **recorded** — read from the data as the scanner wrote it, from an RDSR or from the CTDIvol attribute (0018,9345); - **reconstructed** — absent from the data and rebuilt from the acquisition description and a published coefficient table (§2.3); - **derived** — computed from a quantity already obtained by either route, such as a DLP from a CTDIvol; - **unrecoverable** — neither present nor rebuildable, a required attribute being absent or the scanner untabulated. An outcome, not an error.++

++A series can offer a CTDIvol in more than one place, and the places do not always agree. The candidates are ranked by how directly each records what the scanner delivered — RDSR, image header (0018,9345), dose comment (0040,0310), reconstruction — and the highest is used. **The lower-ranked candidates are not discarded**: each is kept with its numerical difference from the chosen value, so a disagreement between two records of one exposure is visible rather than resolved silently.++

### [added] 2.2 RDSR parsing and its consistency checks

The parser is written against the coded content items of DICOM PS3.16 templates TID 10011–10013 [3], not any vendor's layout, extracting the per-event CTDIvol, ~~DLP and~~ ++DLP,++ reference phantom ~~with~~ ++and technique plus++ the study-level accumulated DLP and event count. Because an RDSR describes an examination rather than one series, attributing an event to a series is explicit — protocol name first, scanning length second — and where ~~none~~ ++no event++ matches ~~unambiguously no recorded value~~ ++unambiguously, nothing++ is taken.

++Two checks are computed for every object rather than assumed: the per-event DLPs are summed and compared with the accumulated DLP the report records separately, read by an independent traversal, and agreement within 1% is required; and the number of events parsed is compared with the total the report declares. Both reference quantities come from different parts of the object from the ones they check, so agreement is evidence neither was misread.++

### [added] 2.3 Physics reconstruction when no recorded dose exists

Where no recorded CTDIvol is ~~available~~ ++available,++ it is reconstructed from an openly licensed table of ~~*measured* normalised~~ ++normalized++ CTDI values: Dinwiddie et al. published under CC BY 4.0 the weighted CTDI per 100 mAs at pitch 1 for 17 scanner models from four manufacturers, tabulated by tube voltage, total collimation, reference phantom and bowtie filter [7]. ++Those values are what the scanner consoles reported, and a console figure is pre-set by the manufacturer from in-factory measurements [7] — a survey of what these scanners display, not an independent measurement of them, which bounds what §2.7 and §4 can claim.++ From one ~~row,~~ ++row of that table,++

### [removed] 2.1 Dose engine, provenance and RDSR parsing

### [removed] 2.2 Physics reconstruction when no recorded dose exists

$$\mathrm{CTDI_{vol}} = \frac{n\mathrm{CTDI_w}}{100}\cdot\frac{I\,t_{rot}}{p}, \qquad \mathrm{DLP} = \mathrm{CTDI_{vol}}\cdot\frac{L_{img}}{10}, \qquad E = \mathrm{DLP}\cdot k,$$

with n\mathrm{CTDI_w} the tabulated value for the series' voltage, collimation, phantom and bowtie, I the mean tube current, t_{rot} the rotation time, p the pitch, L_{img} the imaged scan length in ~~mm~~ ++mm,++ and k the ~~conversion factor~~ ++region- and age-specific DLP-to-effective-dose coefficient++ of ++AAPM Report 96, Table 3 [8]. Those coefficients rest on ICRP Publication 60 tissue weighting, by way of the European Guidelines and the Monte Carlo organ doses Report 96 derived them from;++ ICRP Publication 103 ~~[8].~~ ++[9] revised several weighting factors, so an effective dose reported here is not an ICRP 103 quantity and says so in its own provenance. The sex-specific ICRP 103 coefficients of Deak et al. [10] are also shipped.++ Because the table is ~~measured~~ ++tabulated++ at the voltage and collimation in question, neither an over-beaming factor nor a ~~tube-voltage~~ ++voltage++ extrapolation enters. ~~The reconstructed DLP omits helical over-ranging, so it and the effective dose from it are a conservative underestimate, recorded as an assumption on the value.~~

++The reconstructed DLP uses the imaged scan length and so omits helical over-ranging, the extra rotations at each end that irradiate beyond the reconstructed volume. For a given CTDIvol this biases the DLP low, but does not make it an underestimate overall, because the CTDIvol it is built on carries a two-sided error of its own (§3.3), so neither a DLP nor an effective dose from one is a bound in either direction. The omission is recorded as an assumption on the value rather than presented as a direction of error.++

~~Row selection~~ ++Selecting a row from the coefficient table of Dinwiddie et al. [7]++ is explicit and ~~refusable: manufacturer~~ ++refusable. Manufacturer++ and model must match ++a tabulated model++ exactly or through ~~a short alias list, and~~ ++aliases denoting the same machine;++ a family name identifying no single model is reported as ~~unknown rather than~~ ++unknown, not++ costed against a nearby one. A ++tube++ voltage between ++two++ tabulated ~~voltages~~ ++values++ is interpolated as a power law and refused outside the ~~measured range; an~~ ++tabulated range. An++ untabulated collimation takes the nearest ~~measured value, named in~~ ++one. No DICOM attribute records++ the ~~provenance. DICOM records no~~ bowtie filter, so the ++one++ conventional ~~one~~ for the phantom is ~~assumed.~~ ++assumed — small at 16 cm, medium at 32 cm. Every such substitution is recorded on the value.++

### [added] 2.4 Size-specific dose estimate

The water-equivalent diameter is computed from an automatically extracted body contour as D_w = 2\sqrt{(\overline{\mathrm{HU}}/1000 + 1)\,A_{ROI}/\pi} ~~[9],~~ ++[11],++ using a fixed attenuation threshold, largest-connected-component selection and hole filling, so it is deterministic and needs no deep-learning dependency; SSDE is \mathrm{CTDI_{vol}} \cdot f(D_w), ~~with~~ f from the AAPM TG-204 tables ~~[10].~~ ++[12].++

### [added] 2.5 Archive sample, and what makes a series recoverable

++The archive sample was drawn from The Cancer Imaging Archive [13] over its public NBIA interface, selected by DICOM `Manufacturer` and sampled round-robin: 400 CT series, 100 per manufacturer, across 92 collections.++

++A series is counted as **dose-recoverable** if a dose index can be obtained by either route: it carries a recorded CTDIvol, or no recorded value survives but the reconstruction of §2.3 can run on it. The latter needs two independent conditions at once — the acquisition described completely enough, meaning tube current, rotation or exposure time, total collimation, slice thickness and slice locations all present, **and** the scanner model present in the coefficient table. A series meeting neither is unrecoverable. These conditions are read from the header alone and involve no dose calculation, so they are reported separately from accuracy.++

### [added] 2.6 Accuracy against the archive's own recorded values

++A series already carrying a recorded CTDIvol supplies a reference the archive provides itself: the reconstruction is run as though the value were missing and compared with it. Of the 194 screened series carrying one, 53 sat on a scanner in the coefficient table, and 46 of those also had complete acquisition parameters. **All 46 were evaluated**: the eligible set is defined by the table's coverage rather than sampled, so there is no cap and no seed. Implausible recorded values were excluded beforehand by a physical-range test, not by eye. The pre-specified primary stratum is the subset whose header records the total collimation, a substituted width confounding the dose model with the archive's metadata gaps. Relative error is (reconstructed − recorded) / recorded, so the reference stands in the denominator, and confidence intervals are percentile bootstrap intervals over cases.++

### [added] 2.7 Accuracy on GE, against the scanners' own dose reports

++No GE series in the archive sample carries a recorded CTDIvol (§3.2), so a second reference was taken from the RDSR objects of §2.2, which record both the CTDIvol a GE scanner computed and the technique behind it. Eligibility was fixed before any prediction and every excluded event is counted with its reason: an event enters if it records a CTDIvol, if its scanner is tabulated, if the table moves — spiral and sequenced both qualify, stationary does not — and if the technique is complete. Supplementary §S2.5 gives the criteria in full with the exclusions.++

++**The report carries a measurement that constrains the geometry it claims.** DCM 113824 records the event's total exposure time $T$, independent of the collimation the report states, and DICOM defines the scanning length $L$ (113825) as the table travel over the entire tube loading, so the two span the same interval and the table speed gives $w = L\,t_{rot}/(T\,p)$ — the width the exposure used, from four recorded numbers. Nothing is corrected: $w$ is reported beside the recorded one.++

++**$w$ refutes, and does not select.** The four refuted events differ from their measured width by 86% to 87% and the corroborated one by 0.6%, so saying the recorded field is not a beam width needs no precise $w$; saying which tabulated row the scan used would, and nothing in the report establishes that (§S2.5). So where $w$ contradicts the recorded width the collimation is undetermined and the span runs over every tabulated row, with the narrower span that follows from applying the same margin to the candidate rows reported beside it, since the report does not determine which reading to take. Where $w$ agrees, the recorded width stands. **DICOM records no bowtie filter**, so it varies throughout.++

++**What this arm does and does not establish.** It tests the dose model — table coefficient, mAs per rotation, pitch — and does **not** test recovery from a de-identified header, the technique here coming from a structured report rather than a stripped one. Nor does it test the vendor's dosimetry, the reference and the coefficients both being console figures from the same factory calibrations (§2.3). The events are not independent patients: several share a study, scanner and session.++

### [added] 2.8 Data and reproducibility

The RDSR ~~validation~~ set is the sample collection distributed with OpenREM (GPL-3.0) [4]: 15 ~~DICOM~~ objects from four manufacturers, 14 of them CT RDSRs, read from a user-supplied ~~directory and~~ ++directory,++ not redistributed. ~~The archive sample was drawn from The Cancer Imaging Archive [11] over its public NBIA interface, selected by DICOM `Manufacturer` and sampled round-robin across collections. Every number here is written by a script into result files that ship with the software and re-derived from them by automated tests, so no stale or hand-edited figure passes continuous integration.~~

++Every number reported in this note is written into a result file by the script that computes it, and those files ship with the software. The manuscript's values are checked against them by the automated test suite on every change, so a value edited by hand or left by an earlier run disagrees with its file and fails. The figures are regenerated from the same files.++

### [removed] 2.3 Size-specific dose estimate

### [removed] 2.4 Data and reproducibility

## 3. Results

### 3.1 Recorded dose is read correctly across manufacturers

Of 15 sample objects, 14 parsed as CT Radiation Dose Structured Reports; the fifteenth is a secondary-capture dose screen and was ++correctly++ rejected. The 14 span 4 manufacturers (GE, Siemens, Philips, Canon/Toshiba) and 9 scanner ~~models and hold~~ ++models, holding++ 67 irradiation ~~events,~~ ++events++ of which 44 carry a CTDIvol spanning 0.14–222.59 mGy; both reference phantoms occur (Figure 1A). ~~Two~~ ++Both++ consistency checks ~~were computed per file rather than assumed:~~ ++of §2.2 held: the summed++ per-event DLPs ~~summed to~~ ++agreed with++ the independently recorded accumulated DLP ++to++ within 1% in 14 of 14 comparable files (Figure 1B), and ++the number of++ events parsed equalled the number ~~the report declares~~ ++declared++ in 14 of 14 files.

### 3.2 Recorded-dose availability in a public archive

~~Four hundred CT series were screened, 100 per manufacturer, across 92 collections.~~ CTDIvol was absent from the image header in 206 of ++the++ 400 ++screened++ series (51.5%), but the pooled figure conceals the dominant effect (Figure 2A): 100.0% for GE, 48.0% for Siemens, 34.0% for Philips and 24.0% for Canon/Toshiba. Availability ++of a recorded CTDIvol++ was strongly associated with manufacturer (\chi^2(3) = 137.2, nominal p < 0.001, Cramér's V = 0.59); because series from ~~the same~~ ++one++ collection may not be independent, that p-value should be read cautiously and the effect size ~~emphasised.~~ ++emphasized.++ No series carried a usable DLP record in the dose comment.

~~A series with no recorded dose can still be surveyed if its~~ ++Of the two conditions for reconstruction (§2.5), the++ acquisition ~~is described completely enough *and* its scanner is in the coefficient table: the~~ description was complete enough in 52.5% of the ~~400,~~ ++400 series and++ the scanner ~~in the table~~ ++tabulated++ in 28.8%. Of the 206 series that lacked a recorded CTDIvol, 42 (20.4%) retained enough ~~acquisition~~ metadata to reconstruct one, a conditional rate far more manufacturer-dependent than the marginal one (Table 1). By either route 236 of 400 series (59.0%) were dose-recoverable, leaving 164 (41.0%) ~~that~~ no method here can ~~survey.~~ ++recover.++

**Table 1.** Recorded-dose availability and reconstructability by manufacturer, in 400 public CT series (100 each).

| Manufacturer | CTDIvol absent | Of those, reconstructable | Conditional rate |

| --- | ---: | ---: | ---: |

| GE | 100 / 100 (100.0%) | 37 | **37.0%** |

| Siemens | 48 / 100 (48.0%) | 0 | **0.0%** |

| Philips | 34 / 100 (34.0%) | 0 | **0.0%** |

| Canon/Toshiba | 24 / 100 (24.0%) | 5 | **20.8%** |

| **All** | **206 / 400 (51.5%)** | **42** | **20.4%** |

Tube current, slice thickness and slice location ~~are near-universal;~~ ++were present in nearly every series;++ total collimation and pitch ~~are~~ ++were++ not, and those two limit feasibility (Figure 2B). Scanner coverage is the larger constraint: the table resolved 115 model names, found 0 ambiguous and did not cover 285, and a further 122 series were described completely enough to reconstruct but sat on a scanner ~~it~~ ++the table++ lacks.

### [added] 3.3 Accuracy against the archive's own recorded values

~~A~~ ++Pooled over all 46 evaluated++ series ~~that already carries~~ ++the median relative error was −8.7%, with++ a ~~recorded CTDIvol gives a reference the archive supplies itself. Of the 194 screened series carrying one, 53 sat on a scanner in the coefficient table and~~ ++mean absolute error of 21.7%. The++ 46 ~~of those also had complete acquisition parameters. All 46 were evaluated: the eligible set is defined by the table's coverage rather than sampled. They~~ comprise 45 distinct (recorded, reconstructed) pairs and divide Canon/Toshiba 27, Siemens 12, Philips 7, GE 0 — GE contributing nothing not for want of coverage, ~~it~~ being the best-covered manufacturer, but because no GE series in the sample carries a recorded CTDIvol at all (100.0% ~~absence).~~ ++absence); §3.4 addresses GE separately.++

~~Pooled over all 46~~ ++In++ the ~~median relative error was −8.7%, with a mean absolute error of 21.7%. The stratum isolating the dose model from the archive's metadata gaps is the one~~ ++pre-specified primary stratum,++ whose ~~header records~~ ++headers record++ the total collimation: n = 40, median relative error −5.7% (95% percentile-bootstrap CI −12.4% to +7.8%), mean absolute error 22.0%, ++and++ 60.0% of cases within ±20% (Figure 3). The interval contains zero, so the ~~spread, not~~ ++spread rather than++ the ~~centre,~~ ++center++ is the result; of the 40, 39 are distinct pairs from 21 collections, so it may underestimate uncertainty.

++**The error is two-sided, not conservative.** Reconstruction over-estimated the recorded CTDIvol in 18 of the 46 cases and under-estimated it in 28, spanning −59.4% to +87.4%; the largest over-estimate reconstructed 16.68 mGy where the scanner recorded 8.90 mGy (Figure 3A). A reconstructed CTDIvol therefore cannot be treated as a lower bound, nor can a DLP or effective dose derived from one.++

~~No DICOM attribute records the~~ ++With no++ bowtie ~~filter, so~~ ++recorded,++ 45 of the 46 assumed the ++conventional++ filter ~~conventional for the selected phantom~~ and seven also took the nearest tabulated collimation: no case matched a tabulated technique exactly. Per-manufacturer and per-stratum ~~error~~ detail, the size-correction and effective-dose results, the uncovered ~~model names~~ ++models++ and the data-quality defects found in the published ~~reference~~ tables are in Supplementary ~~Material.~~ ++Material (§S2).++

### [added] 3.4 Accuracy on GE, against the scanners' own dose reports

++The three GE objects hold 35 irradiation events, 15 of which record a CTDIvol. By the criteria of §2.7, 4 of those 15 were excluded because the table does not move, 4 for a geometry that contradicts their recorded type, and 2 because their scanner is a LightSpeed RT16, the radiotherapy configuration (§S2.5); 5 events from 2 scanner models were evaluated (Table 2).++

++**The scanner's own exposure time refutes the collimation four of the five events record.** Both objects state 2.5 or 5.0 mm on 64-slice scanners, where the measured time gives 18.6 to 36.4 mm — a difference of 86% to 87%; for the fifth it gives 39.8 mm against a recorded 40.0 mm, agreeing to 0.6%. On the four, the recorded field is not a beam width — most plausibly a detector row or a nominal reconstructed thickness — a defect in what the scanner writes, not in the table.++

++**Where the recorded width is refuted the span runs over every tabulated row; where it stands, over the filter alone** (Table 2). The four refuted events span −18.2% to +22.4%, or −12.9% to +3.8% under the narrower reading, and the corroborated one −0.0% to +12.7%, its recorded 40 mm row reproducing the recorded value exactly (22.26 mGy against 22.26 mGy, −0.0%) where giving that row up too would reach +35.3%. **Some admissible combination reproduces all five recorded values to within 0.5%** — the pre-stated condition for counting the arithmetic reproduced, against a 1% tolerance from the rounding of the two numbers compared, met under either reading (§S2.5). Inverting the formula gives an implied coefficient of 8.48 to 8.74 mGy per 100 mAs across the four 32 cm events, narrower than any of these spans, placing the residual in the table lookup rather than the formula (§S2.5).++

++**Table 2.** Reconstruction against the CTDIvol recorded by GE scanners in their own dose reports, for the five events meeting the criteria of §2.7, at 120 kVp. The measured collimation is $L\,t_{rot}/(T\,p)$. Where it contradicts the recorded width, that width is refused and the span covers every tabulated collimation and filter; where it agrees, the recorded width stands and only the filter varies.++

++| Scanner | Phantom | Recorded (mGy) | Collimation: stated / measured (mm) | Collimation | Span over assumptions |++

++| --- | ---: | ---: | ---: | --- | ---: |++

++| Optima CT660 | 32 cm | 3.23 | 5.0 / 34.5 | refuted | −12.9% to +21.3% |++

++| Optima CT660 | 32 cm | 5.30 | 5.0 / 36.4 | refuted | −12.1% to +22.4% |++

++| LightSpeed VCT | 32 cm | 8.74 | 2.5 / 19.0 | refuted | −17.9% to +16.4% |++

++| LightSpeed VCT | 32 cm | 4.93 | 2.5 / 18.6 | refuted | −18.2% to +16.1% |++

++| LightSpeed VCT | 16 cm | 22.26 | **40.0 / 39.8** | **stands** | **−0.0% to +12.7%** |++

### [removed] 3.3 Accuracy of the physics reconstruction

## 4. Discussion

Availability differed among manufacturers by up to 76 percentage points. ~~Manufacturer is unlikely to be the whole explanation, since scanner model~~ ++Model++ and export configuration may contribute, but the association is strong enough that any pooled ~~statement that~~ "public CT data lacks CTDIvol" misleads in both directions. ~~Roughly four in ten series admit no estimate by any route, which caps what retrospective dose research on such data can cover.~~

The coefficient constraint is worth separating from the metadata one, because the two have different ~~remedies. Of the 400 series, 122~~ ++remedies: more series++ were ~~described completely enough to reconstruct~~ ++complete++ but ~~sat on a scanner absent from the coefficient table — more~~ ++untabulated++ than ~~the 88~~ the reconstruction could run ~~on.~~ ++on (§3.2).++ Extending an open ~~measured~~ ++coefficient++ database is therefore the single change that would most widen retrospective dose recovery, and it is a metrology task, not a software one. ~~We~~ ++I++ did not close ~~the gap~~ ++it++ by matching an uncovered model onto a nearby covered one: a plausible substitution ~~would convert~~ ++converts++ a refusal into a silent ~~error.~~ ++error, which cost two eligible GE events (§3.4).++

++The GE arm adds a point the archive cannot make: **a recorded acquisition parameter can be wrong, and the same report can show that it is**. Width is the parameter the dose model is most sensitive to, and four of five events state one their own exposure time refutes, so a reconstruction reading that field without a cross-check inherits the error silently. Where the report is self-consistent the table reproduced the scanner's own index exactly; where it is not, the honest output is a span — failing loudly rather than quietly. And a cross-check that refutes a parameter cannot always replace it: that the recorded width is wrong by 86% needs no precision, but naming the row the scan used would need a few per cent the report does not support, so the width becomes undetermined and the range widens rather than narrows.++

Where an RDSR ~~exists our~~ ++exists, this++ parser reads the same content items ~~systems such as~~ OpenREM ~~harvest~~ ++harvests++ [4]; ~~we add~~ ++what is added is++ the case where none does. The contribution is not another CTDIvol calculation but a decision framework: when retrospective recovery is defensible, which assumptions govern it, and when ~~the data support none.~~ ++none hold.++ A reconstructed index is usable because its provenance and uncertainty travel with it, and unusable ~~the moment it is~~ ++once++ quoted as ~~though it had been recorded; it is no substitute for prospective RDSR-based dose monitoring and gives no patient's absorbed dose.~~ ++recorded.++ An ~~optional~~ organ-weighting layer and a patient-specific ~~absorbed-organ-dose~~ extension are the subject of separate work.

++**Limitations.** Every reconstructed value rests on an assumed bowtie filter (§2.7). Scanners carry filters beyond the three the table holds — for pediatric patients, cardiac acquisitions and differently sized bodies — none recorded in DICOM, so the assumption cannot be checked against the data. Its weight is bounded by the table itself: across the 605 comparisons in which more than one filter was tabulated for the same model, voltage, collimation and phantom, substituting a different filter moves nCTDIw by a median of 12.2%, with 82% within 20% and an extreme of +110%. A scanner whose clinical filter departs systematically from the conventional one would be biased throughout.++

++The GE arm is small and indirect: five events on two models, not independent patients, validating the dose model and not recovery from a de-identified header. Only one geometry is corroborated, and every result is a span rather than a point. The GE conditional rate in Table 1 remains unvalidated against a header-derived reference, because none exists. Reconstructed DLP omits over-ranging and is not a bound in either direction (§2.3). Effective doses here carry ICRP 60 weighting through the Report 96 coefficients [8] and would differ under ICRP 103 [9,10]. The RDSR arm rests on 14 objects from one collection with no external ground truth, and the table did not cover 285 of 400 sampled series, so the accuracy arm is neither representative nor a validation across these scanner ranges. Most fundamentally, **neither accuracy arm has an independent reference**: the recorded CTDIvol is a console figure and the coefficient table is a survey of console figures from the same factory calibrations [7], so the agreement tests the arithmetic from metadata to dose index and not the dosimetry beneath it. Where a technique coincides with a tabulated row, as on the corroborated GE event, exact agreement is close to arithmetic.++

~~**Limitations.** **The reconstruction is unvalidated on GE**, no GE series entering the accuracy arm for want of a recorded value to compare against, so the manufacturer it most often applies to is the one whose accuracy is least established. Every reconstructed value rests on an assumed bowtie filter, and 7 of 46 additionally on a substituted collimation. Reconstructed DLP, and the effective dose from it, are underestimates of unquantified size. The RDSR arm rests on 14 objects from one collection with no external ground truth, and the coefficient table did not cover 285 of 400 sampled series, so the accuracy arm is neither representative nor a validation across these scanner ranges — and its reference is an index recorded by the scanner, not an independent measurement.~~

## 5. Conclusions

A CT dose index can be recovered from acquisition metadata when no recorded value survives, but only as far as that metadata and the ~~available~~ open ~~measurements~~ ++coefficient table++ allow, and a recovered value is not interchangeable with a recorded one. Formula-, input-, assumption- and source-level provenance keeps that distinction explicit and the ~~residual~~ uncertainty attributable. A recorded index was missing from roughly half the series screened, and four in ten admitted no estimate at all. ++The error is two-sided, so a recovered index is not a conservative bound. A recorded acquisition parameter can itself be wrong and the same report may show it, so a reconstruction should cross-check what it reads — and check the alternatives too.++ Availability should be reported by manufacturer.

---

## Declarations

**Conflicts of interest.** A potential conflict of interest, arising from a commercial interest in downstream products that may use this software, is disclosed in full on the title page. No patient or customer data were used.

**Funding.** None.

Ethics approval and consent to participate. Not applicable. This study used only publicly available, de-identified imaging data — TCIA collections released under Creative Commons ~~licences [11]~~ ++licenses [13]++ — and third-party sample files, so no review board approval or informed consent was required. No imaging is redistributed: the series ~~analysed~~ ++analyzed++ stay in their public collections and the RDSR samples with their GPL-3.0 distributor, and the coefficient table ~~is redistributed~~ under its own CC BY 4.0 ~~licence~~ ++license++ [7]. No private or internally ~~anonymised~~ ++anonymized++ images were used.

Data and code availability. The software and all numerical results are available in a public repository under the MIT License; the repository URL and the archived-version DOI are withheld here for double-masked review and are provided on the title page. The results reported here, including the rows they ~~summarise,~~ ++summarize,++ ship under `results/` with the scripts that generate them and ++with++ the figure code.

Use of artificial intelligence. A generative AI assistant (Claude, Anthropic) supported software development and drafted portions of this manuscript. All results were produced by executable code in the released repository and ~~were~~ re-run and verified by the author, who takes full ~~responsibility for this work.~~ ++responsibility.++ The AI system is not an author and does not meet authorship criteria.

**Author contributions.** Author contributions are provided on the title page.

---

## References

1. National Council on Radiation Protection and Measurements. *Medical Radiation Exposure of Patients in the United States*. NCRP Report No. 184. Bethesda, MD: NCRP; 2019. 2. International Electrotechnical Commission. *Medical Electrical Equipment — Part 2-44: Particular Requirements for CT*. IEC 60601-2-44. Geneva: IEC. 3. National Electrical Manufacturers Association. *DICOM Standard, Part 16: Content Mapping Resource*. Templates TID 10011–10013, CT Radiation Dose. Rosslyn, VA: NEMA. 4. OpenREM: open-source radiation exposure monitoring [software]. GPL-3.0. https://openrem.org (accessed 27 July 2026). 5. American Association of Physicists in Medicine. *Estimating Patient Organ Dose with Computed Tomography: A Review of Present Methodology and Required DICOM Information*. AAPM Report No. 246. College Park, MD: AAPM; 2019. 6. Stamm G, Nagel HD. CT-Expo — a novel program for dose evaluation in CT. *Rofo*. 2002;174(12):1570–1576. doi:10.1055/s-2002-35937 7. Dinwiddie LE, Baggett JM, Kofler JM, et al. Survey of normalized CTDIvol values across four major computed tomography vendors for use in the MIRDct software. *J Appl Clin Med Phys*. 2026;27(2):e70473. doi:10.1002/acm2.70473 8. ++American Association of Physicists in Medicine. *The Measurement, Reporting, and Management of Radiation Dose in CT*. AAPM Report No. 96. College Park, MD: AAPM; 2008. 9.++ International Commission on Radiological Protection. *The 2007 Recommendations of the ICRP*. ICRP Publication 103. Ann ICRP. 2007;37(2–4). ~~9.~~ ++10. Deak PD, Smal Y, Kalender WA. Multisection CT protocols: sex- and age-specific conversion factors used to determine effective dose from dose-length product. *Radiology*. 2010;257(1):158–166. doi:10.1148/radiol.10100047 11.++ American Association of Physicists in Medicine. *Use of Water Equivalent Diameter for Calculating Patient Size and Size-Specific Dose Estimates (SSDE) in CT*. AAPM Report No. 220. College Park, MD: AAPM; 2014. ~~10.~~ ++12.++ American Association of Physicists in Medicine. *Size-Specific Dose Estimates (SSDE) in Pediatric and Adult Body CT Examinations*. AAPM Report No. 204. College Park, MD: AAPM; 2011. ~~11.~~ ++13.++ Clark K, Vendt B, Smith K, et al. The Cancer Imaging Archive (TCIA): maintaining and operating a public information repository. *J Digit Imaging*. 2013;26(6):1045–1057. doi:10.1007/s10278-013-9622-7

---

## Figure legends and figures

Figure 1. ~~Vendor-neutral~~ ++*Vendor-neutral++ parsing of recorded dose from real CT Radiation Dose Structured Reports. (A) Per-event CTDIvol from 14 objects spanning four manufacturers; marker shape is the ++stated++ reference ~~phantom stated.~~ ++phantom.++ (B) Sum of the per-event DLPs against the accumulated DLP recorded independently in the same object.

![Figure 1](figures/fig1_layer1_rdsr_agreement.png)

Figure 2. ~~Recorded-dose~~ ++*Recorded-dose++ availability in 400 public CT series, 100 per manufacturer, across 92 TCIA collections. (A) Percentage with CTDIvol absent from the header, with metadata complete enough to reconstruct it, and ~~with the dose~~ recoverable ~~by~~ either ~~route.~~ ++way.++ (B) Percentage carrying each acquisition attribute.

![Figure 2](figures/fig2_recorded_dose_availability.png)

Figure 3. ~~Accuracy~~ ++*Accuracy++ of the reconstruction against the scanner's recorded CTDIvol (n = 46). (A) Reconstructed against recorded, identity line dashed, factors of two dotted; circles are the ~~headline~~ ++primary++ stratum. ++Points above the identity line are the 18 over-estimates.++ (B) Relative-error distribution by stratum, median marked.

![Figure 3](figures/fig3_fallback_error.png)
