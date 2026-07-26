# paper/

Working directory for the manuscript and its figures. Deliberately empty of prose so
far — nothing here should be written ahead of the numbers it reports.

## What belongs here

- `manuscript/` — the manuscript source.
- `figures/` — figure-generation scripts. Every figure must be produced by a script in
  this directory from data the repository can reach (the bundled fixture, or a public
  archive path passed as an argument), so that a reader can regenerate it.
- `submission/` — cover letter, disclosure forms, data-availability statement.

## Measured — cite from `results/`, never retype

| Claim | Source of the number |
| --- | --- |
| The RDSR parser reads recorded dose correctly across four vendors | `results/rdsr_layer1.json` → `summary`. Includes two internal consistency checks (Σ per-event DLP vs the SR's own accumulated total; declared vs parsed event count). |
| How often public CT series lack `CTDIvol`, per vendor | `results/survey.json` → `tag_absence.by_vendor[*].ctdivol_absence_rate`, with `n_series_readable` as the denominator. |
| How often the physics reconstruction is even feasible | same file → `fallback_feasibility_rate` and `surveyable_rate`. The complement of `surveyable_rate` is the fraction of the archive no method can recover. |
| How accurate the physics reconstruction is | same file → `fallback_error.diagnostic_and_collimation_measured`. Report the distribution (median, IQR, within ±20 %/±50 %); do not quote a single correction factor. Give the pooled `overall` too, and say what the strata exclude. |
| Why the pooled error is wider than that | same file → `by_collimation_provenance` and `vendor_by_collimation_provenance`. The wider figure measures archive metadata quality, not the dose model — say so explicitly rather than letting a reviewer infer it. |
| Which vendors the accuracy claim does *not* cover | same file → `diagnostic_and_collimation_measured.by_vendor`. A vendor with `n = 0` there is **unvalidated on this archive**; that is a limitation to state in its own sentence, not a gap to leave implicit. |
| Which scanner models the coefficient table does not cover | same file → `tag_absence.unmatched_models`. |

Every figure in the manuscript must be regenerable by a script in `figures/` from these
JSON files. If a number is not in `results/`, it has not been measured and must not be
written.

## Still to measure

| Claim | How it gets measured |
| --- | --- |
| That automated `D_w` matches manual measurement | Compare `measure_patient_size()` against manually placed ROIs on a sample; report Bland–Altman. |
| That organ weighting is meaningful | `organ.organ_dose_profile()` weights vs published Monte-Carlo organ doses for matched protocols. `organ.scale_to_absolute()` requires the citation for any absolute figure. |
| Whether an RDSR present in an archive changes the answer | Re-run the survey on a collection that ships dose SR objects and compare `recorded_candidates` between the SR and the image header. |

## Disclosures the manuscript must carry

- Affiliation: Institute of One, LISIT Co., Ltd.
- Data: bundled fixture is a dummy-patient phantom acquisition; public archive data
  remains under the TCIA Data Usage Policy and each collection's own licence. State
  collection names, licences and access dates.
- Ethics: publicly available de-identified data; no new IRB approval.
- AI: TotalSegmentator (Apache-2.0) for inference only; generative AI used as a
  development aid.
- The three data-quality findings in the reference implementation (see the README) are
  reported, not silently corrected, and belong in the methods.
- Layer-1 sample objects are OpenREM test data (GPL-3.0); they are not redistributed,
  only measurements read from them. Cite OpenREM.
- Layer-2 series are identified in `results/survey.csv` by Series Instance UID with
  their per-series licence; the data-availability statement is written from that column
  plus the access dates in the file's `provenance` block.
