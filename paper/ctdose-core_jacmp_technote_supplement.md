# Supplementary Material

**An open-source, provenance-aware framework for reconstructing missing CT dose indices from DICOM metadata**

Technical Note — supplementary methods and results. Section numbers in the form §3.3 refer to the main text. This file is anonymous for double-masked review; author, affiliation, repository URL and archive DOI are on the title page.

Every number below is written by a script into the machine-readable result files that ship with the software and is re-derived from them by the automated test suite, on the same terms as the main text. No figures accompany this file.

---

## S1. Supplementary methods

### S1.1 Verification of the size layer

The water-equivalent diameter implementation (§2.3) is verified against synthetic phantoms with an analytically known answer: a uniform 0 HU disc of radius $r$ must return $D_w = 2r$ through the segmentation, and doubling the attenuation must scale it by $\sqrt{2}$. The contour excludes the table and detached support hardware and retains interior gas, as the definition requires [S2]. Where the head conversion applies, the closed-form $f = 1.9852\exp(-0.0486 D_w)$ is used [S3] rather than the body tables [S1].

### S1.2 Independent verification of the RDSR parser

Beyond the two per-file consistency checks reported in §3.1, a template-blind sweep of the whole SR content tree — every occurrence of the relevant code values, wherever it sits — returned in every file exactly what the template-navigating parser returned. That sweep is an independent oracle, agreeing only if the parser has neither dropped a branch nor read from the wrong container; it also caught one branch the parser was skipping.

### S1.3 Auditable DRL comparison

Measured indices are compared against diagnostic reference levels [S4, S5]; each comparison emits an alert stating the metric, the reference set and examination, the numeric excess, the ratio, the size band and the provenance of the measured value, with no classifier score. Because a DRL applies to the median of a representative sample and not to an individual examination, that caveat rides on every alert.

### S1.4 Sample licensing and provenance

Each series in the archive sample is identified in the released results by Series Instance UID and by the license the archive states — Creative Commons Attribution 3.0 and 4.0 and their non-commercial variants, with 16 of 400 carrying no license label. The seed, sample sizes and access timestamps are in the released result files. All collections used are public and de-identified, and no imaging is redistributed.

---

## S2. Supplementary results

### S2.1 Scanner models the coefficient table does not cover

Of the 400 screened series the coefficient table resolved 115 model names, found 0 ambiguous and did not cover 285 (§3.2). The commonest uncovered names were `Aquilion` (64 series), `Brilliance 64` (34), `Sensation 64` (18) and `SOMATOM Definition AS` (15). The first is a family name identifying no single model and is reported as unknown rather than assigned to a nearby one: a `SOMATOM Definition AS+` is not an `AS20`, and a plausible substitution would convert a refusal into a silent error.

### S2.2 Stratified accuracy detail

The main text reports the pooled and headline strata of the accuracy arm (§3.3). The remaining strata are given here.

Every recorded value in the set of 46 is a diagnostic acquisition (recorded CTDIvol of at least 0.5 mGy), so no localiser had to be removed and the diagnostic-only stratum leaves 46 cases unchanged. The headline stratum (n = 40, those whose header records the total collimation) had an interquartile range of −15.8% to +11.2%, a full range of −59.4% to +87.4%, and 92.5% of cases within ±50%. Its per-manufacturer medians were Canon/Toshiba (n = 21) −9.4%, Philips (n = 7) −11.7% and Siemens (n = 12) +9.7%.

Collimation provenance no longer dominates the error as it did under a formulation carrying an over-beaming factor growing as $1/h_{col}$: the present route selects a measured row by collimation, so a substitution moves the answer only as far as the neighbouring row. 6 cases had to rebuild the collimation, all Canon/Toshiba, with a median error of −15.8% against −5.7% for the 40 that recorded it — a difference not separable from the manufacturer it is confounded with. Siemens contributed 12 with a recorded collimation and none without, Canon/Toshiba 21 with and 6 without, Philips 7 with and none without.

Replacing the coefficients with measured, openly licensed ones settled a licensing question and also improved accuracy. On the same archive, the superseded run — modeled coefficients with an over-beaming term and a tube-voltage extrapolation, its result files preserved in the repository's history — gave +18.9%, a mean absolute error of 34.9% and 43.5% within ±20%; the measured coefficients give −5.7%, 22.0% and 60.0%.

### S2.3 Size correction and effective dose on the surveyed series

The same 46 series were passed through the size-correction and effective-dose layers, so those components are evaluated on archive data rather than only described. Of the 46, one could not be contoured, its images being reconstructed at two matrix sizes within one series, leaving 45; we excluded 3 scans of physical test objects (radiomics or QIBA phantom collections, whose water-equivalent diameter is valid but is not a patient size) and 1 series of fewer than 10 slices, leaving **41 patient-like series**. Excluded series are kept in the released data with the flag that excluded them.

Across those 41 the measured water-equivalent diameter had a median of 30.3 cm (interquartile range 26.2–35.3 cm, full range 16.6–48.1 cm), giving TG-204 conversion factors with a median of 1.20 (IQR 1.01–1.41, range 0.71–2.01); SSDE from the recorded CTDIvol had a median of 12.5 mGy (IQR 9.5–16.1 mGy). Because SSDE is CTDIvol times that factor, its spread measures how far a phantom-referenced index departs from a size-corrected one across this cohort.

Effective dose is the clearest case of an index no archive records and these series can nonetheless supply: none carries a DLP, but a CTDIvol — recorded here, reconstructed where none is — an imaged scan length and a published conversion factor suffice to rebuild it. It could be computed for **39 of the 41**. The two exceptions are a finding in themselves: all their slices report an identical longitudinal position, so the scan range collapses to zero. SSDE has no longitudinal dependence, but DLP and anything derived from it are not recoverable. Across the 39, effective dose had a median of 4.6 mSv (IQR 3.0–8.1 mSv, range 0.1–22.2 mSv) — a population estimate for a reference phantom, not an individual patient's dose, and an underestimate to the extent that helical over-ranging is omitted (§2.2).

### S2.4 Data-quality findings, reported rather than corrected

Building the reference tables and analyzing real data surfaced defects in the inputs. None was silently corrected; each is flagged in the shipped data, carries a regression test and is reported here.

In the published reference tables: one AAPM TG-204 measured-dimension conversion factor disagrees with the effective-diameter table at the same effective diameter, found by cross-checking the two table families, and the shipped value is marked unreliable rather than adjusted; and the published annex of the coefficient database [S6] contains two typographical variants of scanner model names, which the alias list of §2.2 absorbs explicitly rather than by fuzzy matching. Recorded values are screened against a plausibility range before use as a reference, so a physically impossible one is excluded and counted rather than averaged in; none of the 46 cases triggered it.

One pediatric chest CTDIvol DRL literal reads 141 mGy, inconsistent with adjacent age bands. The primary source settles it: the nationwide survey underlying the J-RIME 2015 pediatric DRLs reports a 75th-percentile CTDIvol of 14.3 mGy and DLP of 296.0 mGy·cm for chest CT at 1–5 years on the 16 cm phantom [S7], so the intended value is 14 mGy and 141 is a single-digit insertion. The shipped table carries the corrected value, the source literal and that citation, so the correction can be checked rather than taken. No pediatric DRL comparison is reported in this work.

A third finding concerns the DLP-to-effective-dose coefficients. The shipped table credited them to ICRP Publication 103; they are AAPM Report 96, Table 3, which rests on ICRP 60 tissue weighting. Checking the thirty values against the report itself while correcting the attribution found one mistranscribed cell, head and neck at one year old, which read 0.0065 against the published 0.0085. No result reported in this work or its supplement changes: the evaluated cohort uses the chest row for 44 cases and the head row for one. The corrected value, the superseded attribution and this reasoning are all recorded in the shipped data file, and every cell is now held against the published table by a regression test.

### S2.5 The GE validation arm in full

Main-text §3.4 reports the five evaluated events. The complete criteria and accounting for the three GE dose reports are given here.

**Eligibility.** An event enters if it records a CTDIvol (DCM 113830); if its scanner model resolves in the coefficient table exactly or through an alias denoting the same machine; if the table moves; and if the tube voltage, mean tube current, exposure time per rotation, pitch, total collimation and CTDIw phantom are all recorded.

The table condition is the one that discriminates, and it is not satisfied by the acquisition-type label alone. In DICOM CID 10013 a *spiral* acquisition spreads one rotation's output along a continuous feed, and a *sequenced* acquisition is axial step-and-shoot in which the table advances between rotations; both are described by CTDIw × (mAs per rotation / 100) / pitch, the second with pitch equal to the table increment divided by the beam width. A *stationary* acquisition is not: the scanner accumulates CTDIvol over the whole dwell, so the exposure time per rotation does not describe the exposure behind the recorded figure, and comparing a single-rotation formula against it would report the difference as model error. One stationary event records 176.12 mGy from 80 mAs per rotation, which the formula would under-predict by more than an order of magnitude. Conversely, a sequenced label does not by itself establish that the table moved: four events carry a recorded pitch of 0.100 over a scanning length of 0.0 mm, which is a cine series at one couch position, and they are excluded for that geometry rather than for their type.

**Accounting.** Of 35 irradiation events, 20 record no CTDIvol and so offer nothing to compare against; these are predominantly localizer and scout exposures. Of the 15 that do, 4 are stationary, 4 have the degenerate geometry just described, and 2 are excluded on the scanner model — a LightSpeed RT16, the 80 cm-bore radiotherapy configuration, not the 70 cm-bore diagnostic LightSpeed 16 the table holds. The alias list declines near misses of exactly this kind, so those two events are lost rather than attributed to a different machine's measured output. Five events remain.

**The exposure-time check.** DCM 113824 records the event's total exposure time $T$, which the scanner measured and which does not depend on the collimation the report states. DICOM Table D-1 defines the scanning length $L$ (113825) as the "Length of the table travel during the entire tube loading, according to IEC 60601-2-44", noting that it "might be longer than the programmed acquisition length (Length of Reconstructable Volume)". $L$ and $T$ therefore span the same interval, over-ranging included, so the table speed $L/T$ equals $w\,p/t_{rot}$ and $w = L\,t_{rot}/(T\,p)$ — the width the exposure used, from four recorded numbers and no assumption, limited only by their rounding.

Per event: Optima 660 at a recorded 5.0 mm gives 34.5 and 36.4 mm; LightSpeed VCT at a recorded 2.5 mm gives 19.0 and 18.6 mm; LightSpeed VCT at a recorded 40.0 mm gives 39.8 mm, which agrees to 0.6%. Four recorded widths are therefore refuted by the scanner's own clock and one is corroborated. The two groups differ by more than two orders of magnitude in relative difference (0.6% against 86% to 87%), so the split is not an artefact of where the line is drawn — each event clears its own uncertainty, derived below, by at least a factor of four in whichever direction it falls.

**Why a range, and over what.** Two inputs are not determined by the report. No DICOM attribute records the bowtie filter, and where the filter conventional for the phantom is not tabulated for a model the choice among those that are is arbitrary — for the Optima 660 at the 32 cm phantom the small and large filters are equidistant from the conventional medium, and they differ by about 14%. The collimation is recorded but refuted on four events by the measurement above.

**That measurement refutes the recorded width; it does not select a replacement.** The two things need very different precision. At a difference of 86% to 87% the recorded field is not a beam width whatever view one takes of how well $w$ is known. Choosing among the table's 10, 20 and 40 mm rows would need $w$ good to a few per cent, and the report does not support that: any figure for its precision is dominated by the rotation time, which GE records as a discrete console setting — 0.4, 0.5, 0.6 or 0.7 s for these five events — rather than a value rounded from a continuum, so the figure would be an assumption and would then decide the arm's answer.

So where the measurement refutes the recorded width, **the collimation is undetermined and every tabulated row stands**, which is what an undetermined input means. Where it agrees, the recorded width stands and the filter is the only assumption left.

Four earlier rules selected a row, and all four are recorded here because each changed the reported spans and each rested on a number chosen rather than measured. Independent review refused them on 2026-11-08.

1. **Nearest row to the recorded width, bowtie tie broken by list order.** For the Optima 660 at 32 cm the conventional medium filter is not tabulated and small and large are equidistant, so the tie fell to the order of a module-level tuple — the head filter for a body phantom. The headline then read "+11.6%, all four within 20%"; under the body filter two of the four leave that band.
2. **Every tabulated row, while the same measurement rejected the recorded one.** Two incompatible positions reported as one: the 10 mm row, which the measurement refutes by factors of 1.9 to 3.6, supplied both values outside 20%.
3. **Every row at or above $w$**, reading the scanning length as the planned range so that over-ranging made $w$ a floor. The definition quoted above contradicts that: $L$ is the whole table travel.
4. **The two rows bracketing $w$** — first exactly, which is discontinuous at the scale of the rounding, since 39.75 mm against a recorded and tabulated 40.0 mm then admitted the 20 mm row the same measurement refutes by half; then within a tolerance propagated from declared input precisions, whose dominant term is the rotation time described above. That version also called the bracket a bound on the coefficient at $w$ on the strength of its endpoints descending, which says nothing about the interior: the table holds three widths and constrains nothing between them.

The result is that this cross-check widens the reported range rather than narrowing it, and that is the finding rather than a disappointment: an index-based reconstruction can establish that a recorded acquisition parameter is wrong without being able to replace it.

Each event is evaluated at every tabulated combination of filter and width, and the released result file carries the whole grid with each cell's error — including the cells outside the bracket — so both the reported span and the wider span over all tabulated widths can be re-derived rather than taken on trust. Over all widths the spans are −12.9% to +21.3% and −12.1% to +22.4% (Optima), −17.9% to +16.4% and −18.2% to +16.1% (VCT 32 cm) and −0.0% to +35.3% (VCT 16 cm).

**Neither reference is independent of the coefficients.** The CTDIvol an RDSR records is the figure the console displayed, which the manufacturer pre-sets from in-factory measurements, and the values in the open table are console figures normalized to 100 mAs from the same calibrations [S6]. What this arm tests is therefore the arithmetic that turns recorded metadata into a dose index — model matching, the voltage, collimation and filter lookup, mAs per rotation from current times time, and the division by pitch — and not the dosimetry either number rests on. It also means that where an event's technique coincides with a tabulated row, exact agreement is close to arithmetic: the one corroborated event has an implied coefficient of 15.900 mGy per 100 mAs against a tabulated 15.9 at the 40 mm row, which is why §3.4 reports that agreement as corroboration of the arithmetic and not as a validation of the model.

---

## S3. Supplementary references

S1. American Association of Physicists in Medicine. *Size-Specific Dose Estimates (SSDE) in Pediatric and Adult Body CT Examinations*. AAPM Report No. 204. College Park, MD: AAPM; 2011.

S2. American Association of Physicists in Medicine. *Use of Water Equivalent Diameter for Calculating Patient Size and Size-Specific Dose Estimates (SSDE) in CT*. AAPM Report No. 220. College Park, MD: AAPM; 2014.

S3. American Association of Physicists in Medicine. *Size-Specific Dose Estimate for Head CT*. AAPM Report No. 293. College Park, MD: AAPM; 2019.

S4. Japan Network for Research and Information on Medical Exposure (J-RIME). *Diagnostic Reference Levels Based on Latest Surveys in Japan — Japan DRLs 2015*. Tokyo: J-RIME; 2015.

S5. Japan Network for Research and Information on Medical Exposure (J-RIME). *National Diagnostic Reference Levels in Japan — Japan DRLs 2020*. Tokyo: J-RIME; 2020.

S6. Dinwiddie LE, Baggett JM, Kofler JM, et al. Survey of normalized CTDIvol values across four major computed tomography vendors for use in the MIRDct software. *J Appl Clin Med Phys*. 2026;27(2):e70473. doi:10.1002/acm2.70473

S7. Takei Y, Miyazaki O, Matsubara K, et al. Nationwide survey of radiation exposure during pediatric computed tomography examinations and proposal of age-based diagnostic reference levels for Japan. *Pediatr Radiol*. 2016;46(2):280–285. doi:10.1007/s00247-015-3474-x
