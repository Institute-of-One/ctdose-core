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

Each series in the archive sample is identified in the released results by Series Instance UID and by the licence the archive states — Creative Commons Attribution 3.0 and 4.0 and their non-commercial variants, with 16 of 400 carrying no licence label. The seed, sample sizes and access timestamps are in the released result files. All collections used are public and de-identified, and no imaging is redistributed.

---

## S2. Supplementary results

### S2.1 Scanner models the coefficient table does not cover

Of the 400 screened series the coefficient table resolved 115 model names, found 0 ambiguous and did not cover 285 (§3.2). The commonest uncovered names were `Aquilion` (64 series), `Brilliance 64` (34), `Sensation 64` (18) and `SOMATOM Definition AS` (15). The first is a family name identifying no single model and is reported as unknown rather than assigned to a nearby one: a `SOMATOM Definition AS+` is not an `AS20`, and a plausible substitution would convert a refusal into a silent error.

### S2.2 Stratified accuracy detail

The main text reports the pooled and headline strata of the accuracy arm (§3.3). The remaining strata are given here.

Every recorded value in the set of 46 is a diagnostic acquisition (recorded CTDIvol of at least 0.5 mGy), so no localiser had to be removed and the diagnostic-only stratum leaves 46 cases unchanged. The headline stratum (n = 40, those whose header records the total collimation) had an interquartile range of −15.8% to +11.2%, a full range of −59.4% to +87.4%, and 92.5% of cases within ±50%. Its per-manufacturer medians were Canon/Toshiba (n = 21) −9.4%, Philips (n = 7) −11.7% and Siemens (n = 12) +9.7%.

Collimation provenance no longer dominates the error as it did under a formulation carrying an over-beaming factor growing as $1/h_{col}$: the present route selects a measured row by collimation, so a substitution moves the answer only as far as the neighbouring row. 6 cases had to rebuild the collimation, all Canon/Toshiba, with a median error of −15.8% against −5.7% for the 40 that recorded it — a difference not separable from the manufacturer it is confounded with. Siemens contributed 12 with a recorded collimation and none without, Canon/Toshiba 21 with and 6 without, Philips 7 with and none without.

Replacing the coefficients with measured, openly licensed ones settled a licensing question and also improved accuracy. On the same archive, the superseded run — modelled coefficients with an over-beaming term and a tube-voltage extrapolation, its result files preserved in the repository's history — gave +18.9%, a mean absolute error of 34.9% and 43.5% within ±20%; the measured coefficients give −5.7%, 22.0% and 60.0%.

### S2.3 Size correction and effective dose on the surveyed series

The same 46 series were passed through the size-correction and effective-dose layers, so those components are evaluated on archive data rather than only described. Of the 46, one could not be contoured, its images being reconstructed at two matrix sizes within one series, leaving 45; we excluded 3 scans of physical test objects (radiomics or QIBA phantom collections, whose water-equivalent diameter is valid but is not a patient size) and 1 series of fewer than 10 slices, leaving **41 patient-like series**. Excluded series are kept in the released data with the flag that excluded them.

Across those 41 the measured water-equivalent diameter had a median of 30.3 cm (interquartile range 26.2–35.3 cm, full range 16.6–48.1 cm), giving TG-204 conversion factors with a median of 1.20 (IQR 1.01–1.41, range 0.71–2.01); SSDE from the recorded CTDIvol had a median of 12.5 mGy (IQR 9.5–16.1 mGy). Because SSDE is CTDIvol times that factor, its spread measures how far a phantom-referenced index departs from a size-corrected one across this cohort.

Effective dose is the clearest case of an index no archive records and these series can nonetheless supply: none carries a DLP, but a CTDIvol — recorded here, reconstructed where none is — an imaged scan length and a published conversion factor suffice to rebuild it. It could be computed for **39 of the 41**. The two exceptions are a finding in themselves: all their slices report an identical longitudinal position, so the scan range collapses to zero. SSDE has no longitudinal dependence, but DLP and anything derived from it are not recoverable. Across the 39, effective dose had a median of 4.6 mSv (IQR 3.0–8.1 mSv, range 0.1–22.2 mSv) — a population estimate for a reference phantom, not an individual patient's dose, and an underestimate to the extent that helical over-ranging is omitted (§2.2).

### S2.4 Data-quality findings, reported rather than corrected

Building the reference tables and analysing real data surfaced defects in the inputs. None was silently corrected; each is flagged in the shipped data, carries a regression test and is reported here.

In the published reference tables: one AAPM TG-204 measured-dimension conversion factor disagrees with the effective-diameter table at the same effective diameter, found by cross-checking the two table families, and the shipped value is marked unreliable rather than adjusted; and the published annex of the coefficient database [S6] contains two typographical variants of scanner model names, which the alias list of §2.2 absorbs explicitly rather than by fuzzy matching. Recorded values are screened against a plausibility range before use as a reference, so a physically impossible one is excluded and counted rather than averaged in; none of the 46 cases triggered it.

One paediatric chest CTDIvol DRL literal reads 141 mGy, inconsistent with adjacent age bands. The primary source settles it: the nationwide survey underlying the J-RIME 2015 paediatric DRLs reports a 75th-percentile CTDIvol of 14.3 mGy and DLP of 296.0 mGy·cm for chest CT at 1–5 years on the 16 cm phantom [S7], so the intended value is 14 mGy and 141 is a single-digit insertion. The shipped table carries the corrected value, the source literal and that citation, so the correction can be checked rather than taken. No paediatric DRL comparison is reported in this work.

---

## S3. Supplementary references

S1. American Association of Physicists in Medicine. *Size-Specific Dose Estimates (SSDE) in Pediatric and Adult Body CT Examinations*. AAPM Report No. 204. College Park, MD: AAPM; 2011.

S2. American Association of Physicists in Medicine. *Use of Water Equivalent Diameter for Calculating Patient Size and Size-Specific Dose Estimates (SSDE) in CT*. AAPM Report No. 220. College Park, MD: AAPM; 2014.

S3. American Association of Physicists in Medicine. *Size-Specific Dose Estimate for Head CT*. AAPM Report No. 293. College Park, MD: AAPM; 2019.

S4. Japan Network for Research and Information on Medical Exposure (J-RIME). *Diagnostic Reference Levels Based on Latest Surveys in Japan — Japan DRLs 2015*. Tokyo: J-RIME; 2015.

S5. Japan Network for Research and Information on Medical Exposure (J-RIME). *National Diagnostic Reference Levels in Japan — Japan DRLs 2020*. Tokyo: J-RIME; 2020.

S6. Dinwiddie LE, Baggett JM, Kofler JM, et al. Survey of normalized CTDIvol values across four major computed tomography vendors for use in the MIRDct software. *J Appl Clin Med Phys*. 2026;27(2):e70473. doi:10.1002/acm2.70473

S7. Takei Y, Miyazaki O, Matsubara K, et al. Nationwide survey of radiation exposure during pediatric computed tomography examinations and proposal of age-based diagnostic reference levels for Japan. *Pediatr Radiol*. 2016;46(2):280–285. doi:10.1007/s00247-015-3474-x
