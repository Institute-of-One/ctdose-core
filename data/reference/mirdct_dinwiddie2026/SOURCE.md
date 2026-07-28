# MIRDct normalized CTDI database — source record

The per-scanner CTDI coefficients this package ships are extracted from the supplementary
data of an open-access, peer-reviewed article. This file records where they came from, so
the licence position and the extraction are both auditable.

| | |
|---|---|
| **Citation** | Dinwiddie LE, Baggett JM, Kofler JM, Kofler CB, Long DJ, Dawson RJ, Wehmeier SK, Wang Y, Ocampo-Ramos JC, Carter LM, Marquis H, Kayal G, Kesner AL, Bolch WE. Survey of normalized CTDIvol values across four major computed tomography vendors for use in the MIRDct software. *J Appl Clin Med Phys*. 2026;27(2):e70473. |
| **DOI** | [10.1002/acm2.70473](https://doi.org/10.1002/acm2.70473) |
| **PMID / PMCID** | 41588631 / PMC12835190 |
| **Licence** | **Creative Commons Attribution 4.0 International (CC BY 4.0)** |
| **File** | `ACM2-27-e70473-s001.xlsx` (Supporting Information S1) |
| **Sheet used** | `MIRDct` (the workbook also holds `Summary`, `NCICT`, `VirtualDose`) |
| **SHA-256** | `83cf1973490786958e0f732deeaa228b0fcdc90791920779992e30338d68cf68` |
| **Retrieved** | 2026-07-28, via the Europe PMC supplementary-files API for PMC12835190 |
| **Extracted by** | `tools/extract_mirdct_coefficients.py` |

## Licence position

The article is CC BY 4.0, which permits redistribution and adaptation of the data with
attribution. The workbook is therefore included here **unmodified**, and the derived JSON
carries the citation and licence in its provenance block. Attribution appears in three
places a downstream user will actually see: the data file's provenance, `CITATION.cff` /
`.zenodo.json` as a related identifier, and the manuscript's reference list.

**This directory is CC BY 4.0, not MIT.** The rest of the repository is MIT; that does not
extend to third-party data. Anyone redistributing these coefficients must keep the
attribution above.

## What the table contains

1085 rows: 17 scanner models across 4 vendors (Canon 5, GE 6, Philips 2, Siemens 4),
tabulated by tube potential (70–150 kVp), beam collimation (5–80 mm), CTDI phantom
(16 cm head, 32 cm body) and bowtie filter (Small / Medium / Large).

## Two conventions read from the paper, not assumed

**The tabulated quantity is CTDIw, not CTDIvol.** The value column is headed `CTDIw`, and
every row is at **pitch 1.0** and **100 mAs**. At pitch 1 the two coincide, which is why the
paper's title says normalized CTDIvol; the distinction matters when the value is *used*,
because CTDIw is pitch-independent and CTDIvol is not. The conversion is therefore

    CTDIvol = CTDIw_table x (mAs_rot / 100) / pitch

with `mAs_rot` the actual tube current-time product per rotation. The paper's Methods state
that all techniques used "the same effective tube current-time product of 100 mAs and the
same pitch of 1.0", so at pitch 1 the effective and actual mAs coincide and the 100 mAs
normalisation is unambiguous.

**Collimation is tabulated, not modelled.** The old CT-Expo-style route needed an
over-beaming factor `k_OB` to correct a single reference beam width to the collimation
actually used. Here the collimation dependence is measured directly, so `k_OB` is not
applied to values from this table — one modelling assumption removed rather than restated.

## Known transcription details in the source

The model names are used verbatim as the source spells them, including two apparent typos,
so that a reader comparing against the published annex finds exactly the same strings:

- `Aquiliion ONE` (Canon) — likely "Aquilion ONE"
- `Defintion Edge` (Siemens) — likely "Definition Edge"

The matcher in `ctdose_core/scanners.py` handles both the source spelling and the
conventional one; the stored data is not silently corrected.
