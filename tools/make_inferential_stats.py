"""Attach uncertainty to the two headline claims, from the rows already in ``results/``.

The survey reports point estimates: a median reconstruction error, and per-manufacturer
absence rates that differ by up to 76 percentage points. Neither carries an interval, so
a reader cannot tell how much of either is sampling noise. This script adds:

* a **percentile bootstrap confidence interval** on the median of the headline error
  stratum, resampled with a fixed seed so the interval is reproducible rather than
  merely plausible;
* a **chi-squared test of independence** between manufacturer and whether CTDIvol is
  present, with Cramer's V as the effect size, because with n = 400 a p-value alone
  says nothing about how big the difference is.

Both come with a stated caveat about independence, which the sample does not satisfy:
series are drawn round-robin from collections, several collections re-release the same
acquisition, and 3 of the 38 fallback cases are duplicate (recorded, reconstructed)
pairs. Intervals computed as if the observations were independent are therefore
**narrower than the truth**, and the p-value is anti-conservative. They are reported as
a lower bound on the uncertainty, not as an exact one.

Usage::

    python tools/make_inferential_stats.py

Writes ``results/inferential_stats.json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"

#: Fixed so the interval is a property of the data, not of when the script was run.
BOOTSTRAP_SEED = 20260727
BOOTSTRAP_RESAMPLES = 20000


def bootstrap_median(values: list[float], *, seed: int, resamples: int) -> dict[str, Any]:
    """Percentile bootstrap interval for a median.

    The percentile method is used rather than a normal approximation because the error
    distribution is skewed and bounded below by -100%, so a symmetric interval would put
    mass where no value can fall.
    """
    x = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(x, size=(resamples, x.size), replace=True)
    medians = np.median(draws, axis=1)
    lo, hi = np.percentile(medians, [2.5, 97.5])
    return {
        "n": int(x.size),
        "median": float(np.median(x)),
        "ci95_low": float(lo),
        "ci95_high": float(hi),
        "ci_width": float(hi - lo),
        "method": "percentile bootstrap",
        "resamples": resamples,
        "seed": seed,
        "bootstrap_se": float(np.std(medians, ddof=1)),
    }


def vendor_independence(counts: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Chi-squared test of manufacturer against CTDIvol presence, with an effect size."""
    from scipy.stats import chi2_contingency

    vendors = sorted(counts)
    table = np.array([[counts[v]["present"], counts[v]["absent"]] for v in vendors], dtype=float)
    chi2, p, dof, expected = chi2_contingency(table, correction=False)

    n = table.sum()
    # Cramer's V; for a 2-column table min(rows-1, cols-1) = 1, so V = sqrt(chi2/n).
    cramers_v = float(np.sqrt(chi2 / (n * min(table.shape[0] - 1, table.shape[1] - 1))))
    return {
        "vendors": vendors,
        "table_present_absent": table.astype(int).tolist(),
        "chi2": float(chi2),
        "dof": int(dof),
        "p_value": float(p),
        "p_value_formatted": f"< {10 ** -np.floor(-np.log10(p)):g}" if p > 0 else "< 1e-300",
        "cramers_v": cramers_v,
        "min_expected_count": float(expected.min()),
        "n": int(n),
        "note": (
            "Chi-squared test of independence between manufacturer and whether the image "
            "header carries CTDIvol. Cramer's V is reported because with n = 400 a small "
            "p-value is not evidence of a large difference; here the effect size is what "
            "carries the claim. The test assumes independent observations, which this "
            "sample does not satisfy (see 'independence_caveat'), so the p-value is "
            "anti-conservative and should be read as direction and magnitude rather than "
            "as an exact tail probability."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", type=Path, default=RESULTS / "survey.json")
    ap.add_argument("--out", type=Path, default=RESULTS / "inferential_stats.json")
    args = ap.parse_args()

    survey = json.loads(args.survey.read_text(encoding="utf-8"))
    fallback = survey["fallback_error"]
    cases = survey["fallback_cases"]
    threshold = fallback["diagnostic_acquisitions_only"]["threshold_mgy"]

    headline = [
        c["percent_error"]
        for c in cases
        if c["recorded_ctdivol_mgy"] >= threshold and c["collimation_measured"]
    ]
    stored = fallback["diagnostic_and_collimation_measured"]
    if len(headline) != stored["n"]:
        raise SystemExit(
            f"headline stratum rebuilt from the per-case rows has {len(headline)} cases, "
            f"but the stored summary says {stored['n']}. The strata disagree; fix that "
            "before attaching an interval to either."
        )

    absence = survey["tag_absence"]["by_vendor"]
    counts = {
        v: {
            "present": b["with_ctdivol_tag"],
            "absent": b["without_ctdivol_tag"],
        }
        for v, b in absence.items()
    }

    n_cases = fallback["independence"]["n_cases"]
    n_distinct = fallback["independence"]["n_distinct_recorded_estimated_pairs"]

    payload = {
        "provenance": {
            "generated_by": "tools/make_inferential_stats.py",
            "inputs": [str(args.survey.relative_to(REPO))],
            "purpose": (
                "Attach an interval to the headline reconstruction error and a test with "
                "an effect size to the manufacturer difference in recorded-dose "
                "availability, so neither point estimate is quoted bare."
            ),
        },
        "headline_error_median": {
            **bootstrap_median(
                headline, seed=BOOTSTRAP_SEED, resamples=BOOTSTRAP_RESAMPLES
            ),
            "stratum": "diagnostic acquisitions with a recorded collimation",
            "diagnostic_threshold_mgy": threshold,
        },
        "vendor_effect_on_ctdivol_availability": vendor_independence(counts),
        "independence_caveat": {
            "n_fallback_cases": n_cases,
            "n_distinct_pairs": n_distinct,
            "n_duplicate_pairs": n_cases - n_distinct,
            "note": (
                "Public archives re-release the same acquisition under different "
                "collection names, and the screening sample is drawn round-robin from "
                "collections rather than from independent examinations. The effective "
                "sample size is therefore smaller than n in both analyses: the bootstrap "
                "interval is narrower than the true interval, and the chi-squared p-value "
                "is smaller than the true p-value. Both are reported as lower bounds on "
                "the uncertainty."
            ),
        },
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    h = payload["headline_error_median"]
    v = payload["vendor_effect_on_ctdivol_availability"]
    print(
        f"headline median {h['median']:+.1f}% "
        f"(95% bootstrap CI {h['ci95_low']:+.1f}% to {h['ci95_high']:+.1f}%, n = {h['n']})"
    )
    print(
        f"vendor vs CTDIvol presence: chi2({v['dof']}) = {v['chi2']:.1f}, "
        f"p {v['p_value_formatted']}, Cramer's V = {v['cramers_v']:.2f}, n = {v['n']}"
    )
    print(
        f"  (independence not satisfied: {payload['independence_caveat']['n_duplicate_pairs']} "
        "duplicate pairs among the fallback cases; both figures are lower bounds)"
    )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
