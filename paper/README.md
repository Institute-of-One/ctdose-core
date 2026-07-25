# paper/

Working directory for the manuscript and its figures. Deliberately empty of prose so
far — nothing here should be written ahead of the numbers it reports.

## What belongs here

- `manuscript/` — the manuscript source.
- `figures/` — figure-generation scripts. Every figure must be produced by a script in
  this directory from data the repository can reach (the bundled fixture, or a public
  archive path passed as an argument), so that a reader can regenerate it.
- `submission/` — cover letter, disclosure forms, data-availability statement.

## Numbers that are not yet measured

The following are claims the manuscript will need to support, and the measurement that
supports each. None of them may be written until the corresponding measurement exists.

| Claim | How it gets measured |
| --- | --- |
| How often public CT series lack `CTDIvol` | `ctdose survey <archive_root>` over the target collections; report `fraction_needing_fallback` per collection with n. |
| How accurate the physics fallback is | On series that *do* carry `CTDIvol`: `analyze_series(..., prefer_recorded=True)` vs `prefer_recorded=False`, then `metrics.fallback_error()`. Report the signed error distribution, not a single figure. |
| That automated `D_w` matches manual measurement | Compare `measure_patient_size()` against manually placed ROIs on a sample; report Bland–Altman. |
| That organ weighting is meaningful | `organ.organ_dose_profile()` weights vs published Monte-Carlo organ doses for matched protocols. `organ.scale_to_absolute()` requires the citation for any absolute figure. |

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
