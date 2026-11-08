"""The k-factor must not be attributed to ICRP 103 anywhere.

Reviewer 1 of the JACMP submission wrote, of manuscript line 65, "k-factors are not
described in ICRP 103. Please provide an appropriate reference." They were right, and the
error was not confined to the sentence they read. It was in four places in the code, in
the shipped data file's provenance block, and on the ``reference`` field of every
effective dose the package returned -- so a user who followed the provenance of their own
result was sent to a document that does not contain the coefficient.

That is the shape this repository keeps finding: one wrong claim written down in several
places, where fixing the one a reader pointed at leaves the rest. So the attribution is a
gate across the whole tree rather than a correction in one file.

What is being asserted:

* ``kfactor`` is AAPM Report No. 96 (2008) Table 3. Report 96 took it from the European
  Guidelines for Multislice CT and Shrimpton et al. 2006, resting on NRPB Monte Carlo
  organ doses weighted by ICRP Publication 60.
* ``fmean`` is Deak et al. 2010, computed with ICRP Publication 103 weighting.
* ICRP 103 gives the effective-dose formalism and the tissue weighting factors. It does
  not tabulate DLP-to-effective-dose conversion coefficients.
* AAPM Report 204 is the SSDE report. It is used in this package, correctly, for
  size-specific dose estimates, and has nothing to do with this quantity.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Every Python file that could carry the attribution, plus the data file.
SOURCES = sorted(
    p for p in (REPO / "ctdose_core").rglob("*.py")
    if "__pycache__" not in p.parts
) + sorted(
    p for p in (REPO / "tools").rglob("*.py") if "__pycache__" not in p.parts
)

#: A claim that ICRP 103 is the source of the conversion coefficient. The point is to
#: catch the attribution, not the word: ICRP 103 may legitimately be named as what
#: ``fmean`` rests on, or as what the k-factor does *not* rest on.
WRONG_ATTRIBUTION = re.compile(
    r"ICRP[-\s]*(?:Publication\s*)?103[^.\n]{0,60}?"
    r"(?:DLP-to-effective-dose|k[-\s]?factor|conversion coefficient)"
    r"|(?:k[-\s]?factor|DLP-to-effective-dose)[^.\n]{0,40}?ICRP[-\s]*(?:Publication\s*)?103",
    re.IGNORECASE,
)

#: Sentences that deny the attribution, which must not be read as making it.
PERMITTED = (
    "not an ICRP 103 effective dose",
    "previously described the",
    "does not tabulate",
    "not a source of DLP conversion coefficients",
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_source_file_credits_icrp_103_for_the_k_factor(path):
    text = path.read_text(encoding="utf-8")
    for match in WRONG_ATTRIBUTION.finditer(text):
        line_no = text[: match.start()].count("\n") + 1
        line = text.splitlines()[line_no - 1]
        if any(p in line for p in PERMITTED):
            continue
        # The sentence may span lines; check a small window for a denial too.
        window = "\n".join(text.splitlines()[max(0, line_no - 3): line_no + 2])
        if any(p in window for p in PERMITTED):
            continue
        pytest.fail(
            f"{path.relative_to(REPO)}:{line_no} attributes the DLP-to-effective-dose "
            f"coefficient to ICRP 103: {match.group(0)!r}. It is AAPM Report No. 96 "
            f"(2008) Table 3, on ICRP 60 weighting."
        )


def test_the_shipped_reference_string_names_aapm_96():
    """What a user sees on their own result, by both routes that return one."""
    from ctdose_core.effdose import effective_dose
    from ctdose_core.metrics import effective_dose_open
    from ctdose_core.tables import lookup_conversion

    factors = lookup_conversion("Chest", "Adults", "F")
    by_table = effective_dose(500.0, factors)
    by_metrics = effective_dose_open(
        500.0, factors.kfactor, region="Chest", size="Adults", sex="F"
    )
    for quantity in (by_table, by_metrics):
        assert "AAPM Report No. 96" in quantity.reference
        assert "Table 3" in quantity.reference
        assert "ICRP" not in quantity.reference
        assert any("ICRP 60" in n for n in quantity.notes), (
            "the value must carry which tissue weighting produced it"
        )
    # Both routes must agree; they read the same table.
    assert float(by_table) == pytest.approx(float(by_metrics))
    # Chest adult k = 0.014 mSv/(mGy.cm), so 500 mGy.cm is 7 mSv.
    assert float(by_table) == pytest.approx(500.0 * 0.014)
