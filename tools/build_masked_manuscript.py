"""Derive the double-masked review manuscript from the canonical one.

JACMP reviews double-masked, so the article file must carry nothing that identifies the
author. The identity is not deleted from the project -- it stays in the canonical
manuscript, in the repository and on the separately uploaded title page. This script
produces the review-only derivative, and it is a script rather than a hand-edit for two
reasons: a masked build has to be reproducible after every revision, and a hand-edit is
exactly the kind of change that silently misses one occurrence.

What is masked, and why each item identifies the author:

* the author block -- name, affiliation, corresponding author, email;
* the software name, which is the public repository's name and so resolves to the author
  in one search;
* the repository URL and the archive DOIs, for the same reason;
* the conflict-of-interest and author-contribution declarations, which name the author's
  companies and his initials. Both are replaced by a pointer to the title page rather
  than dropped, so the reviewer knows the disclosure exists.

What is deliberately *not* masked: the TCIA collection DOI and reference [18], which
identify public data and not the author; the funding, ethics and AI declarations, which
name no one; and the reference list, in which no entry is the author's own.

Usage::

    python tools/build_masked_manuscript.py            # writes the masked .md and .pdf
    python tools/build_masked_manuscript.py --check    # verify an existing masked PDF

The masked PDF is verified by extracting its text and asserting that no forbidden token
survives, so a missed substitution fails the build instead of reaching an editor.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "paper" / "ctdose-core_jacmp.md"
MASKED_MD = REPO / "paper" / "ctdose-core_jacmp_masked.md"
MASKED_PDF = REPO / "paper" / "ctdose-core_jacmp_masked.pdf"

#: Every token that must not survive into the review PDF. Checked against the extracted
#: text of the built PDF, case-insensitively, because the failure being guarded against
#: is a reviewer recognising the author -- not an exact string match.
FORBIDDEN = (
    "Yamamoto",
    "LISIT",
    "TexelCraft",
    "Institute of One",
    "Institute-of-One",
    "yamamoto@",
    "ORCID",
    "github.com",
    "zenodo",
    "10.5281",
    "ctdose-core",
    "ctdose_core",
)

#: The author block, removed whole. Title, running title, article type and section stay:
#: none of them names anyone.
AUTHOR_BLOCK = re.compile(
    r"\n\*\*Shuji Yamamoto\*\*\n+"
    r"Institute of One, LISIT Co\., Ltd\., Tokyo, Japan\n+"
    r"\*\*Corresponding author:\*\*[^\n]*\n",
    re.MULTILINE,
)

REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # --- the software name, which is also the repository name -------------------------
    (
        "`ctdose-core` has a functional API and a command-line interface",
        "The package has a functional API and a command-line interface",
    ),
    # --- declarations that name the author or his companies ---------------------------
    (
        "**Conflicts of interest.** The author is the representative of LISIT Co., Ltd. "
        "(Tokyo, Japan) and Chief Executive Officer of TexelCraft OÜ, and this work was "
        "conducted under the supervision of LISIT Co., Ltd. The software is released under "
        "the MIT licence and the author has a commercial interest in downstream products "
        "that may use it. No patient or customer data were used.",
        "**Conflicts of interest.** A potential conflict of interest, arising from a "
        "commercial interest in downstream products that may use this software, is "
        "disclosed in full on the title page. No patient or customer data were used.",
    ),
    (
        "**Data and code availability.** The software is available at "
        "https://github.com/Institute-of-One/ctdose-core under the MIT License. The version "
        "used in this study (version 0.1.1) is archived at Zenodo "
        "(https://doi.org/10.5281/zenodo.21636719). All numerical results reported here, "
        "including the rows they summarize, ship under `results/` with the scripts that "
        "generate them and the figure code.",
        "**Data and code availability.** The software and all numerical results are "
        "available in a public repository under the MIT License; the repository URL and the "
        "archived-version DOI are withheld here for double-masked review and are provided "
        "on the title page. The results reported here, including the rows they summarize, "
        "ship under `results/` with the scripts that generate them and with the figure code.",
    ),
    (
        "**Author contributions.** S.Y.: conceptualisation, methodology, software, "
        "validation, formal analysis, investigation, data curation, writing — original "
        "draft, writing — review and editing, visualisation.",
        "**Author contributions.** Author contributions are provided on the title page.",
    ),
)

#: Applied after the explicit replacements, to catch the name wherever it is used as a
#: bare noun in the body. Ordered longest-first so the backticked form is consumed before
#: the bare one.
BARE_NAME = (
    ("`ctdose-core`", "the framework"),
    ("ctdose-core", "the framework"),
    ("ctdose_core", "the package"),
)


#: Spelling variants that must not decide whether a declaration is found. The passages
#: below are matched exactly, which is the right default -- a loose match on a
#: declaration that names the author's companies is how one occurrence gets through. But
#: it made the whole masking fail on a copy-edit: converting the manuscript to US
#: spelling for revision 2 changed "MIT licence" to "MIT License" and "summarise" to
#: "summarize", and the build then refused the whole document. The two manuscripts in
#: this repository now differ in spelling, the Research Article being preserved as
#: submitted, so the match is made spelling-insensitive rather than pinned to either.
SPELLING_VARIANTS = (
    ("licence", "license"),
    ("summarise", "summarize"),
    ("summarises", "summarizes"),
    ("conceptualisation", "conceptualization"),
    ("visualisation", "visualization"),
    ("normalised", "normalized"),
    ("analysed", "analyzed"),
    ("organisation", "organization"),
)


def _spelling_insensitive(passage: str) -> re.Pattern[str]:
    """A pattern matching ``passage`` with either spelling of each listed word."""
    parts: list[str] = []
    for token in re.split(r"(\w+)", passage):
        alternatives = {token}
        for british, american in SPELLING_VARIANTS:
            if token.lower() in (british, american):
                alternatives = {british, american}
        if len(alternatives) > 1:
            parts.append(
                "(?:" + "|".join(re.escape(a) for a in sorted(alternatives)) + ")"
            )
        else:
            parts.append(re.escape(token))
    # Case-insensitive, because "MIT licence" became "MIT License" and the capital was
    # enough to fail the match. These passages are whole declarations and distinctive
    # enough that ignoring case adds no risk of hitting the wrong one.
    return re.compile("".join(parts), re.IGNORECASE)


def mask(text: str) -> str:
    """Return the review manuscript: same science, no author."""
    text, n = AUTHOR_BLOCK.subn("\n", text)
    if n != 1:
        raise SystemExit(
            f"expected exactly one author block to remove, found {n}. The top matter has "
            "changed shape; update AUTHOR_BLOCK rather than letting the block through."
        )
    for old, new in REPLACEMENTS:
        pattern = _spelling_insensitive(old)
        found = pattern.search(text)
        if found is None:
            raise SystemExit(
                "a passage this script masks is no longer in the manuscript, so the "
                "masking is out of date with the source. Missing:\n\n" + old[:160]
            )
        text = text[: found.start()] + new + text[found.end() :]
    for old, new in BARE_NAME:
        text = text.replace(old, new)
    return text


def extract_text(pdf: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf)).pages)


def verify(path: Path) -> list[str]:
    """Every forbidden token that survived into a built artefact.

    Takes a PDF or a Markdown file. The Markdown case matters because the frozen masked
    manuscript of a previous round is a review-facing *input* -- the tracked-changes diff
    is built from it -- so anything identifying in it reaches the reviewer through the
    diff even though the file itself is never uploaded.
    """
    body = (
        path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".md", ".txt")
        else extract_text(path)
    ).lower()
    return [token for token in FORBIDDEN if token.lower() in body]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out-md", type=Path, default=MASKED_MD)
    ap.add_argument("--out-pdf", type=Path, default=MASKED_PDF)
    ap.add_argument("--check", action="store_true",
                    help="verify the existing masked PDF without rebuilding it")
    args = ap.parse_args()
    # Resolved so a relative path on the command line -- the natural way to name the
    # Technical Note variant -- still reports itself relative to the repository below.
    args.source, args.out_md, args.out_pdf = (
        p.resolve() for p in (args.source, args.out_md, args.out_pdf)
    )

    if not args.check:
        masked = mask(args.source.read_text(encoding="utf-8"))
        args.out_md.write_text(masked, encoding="utf-8", newline="")
        print(f"wrote {args.out_md.relative_to(REPO)}  ({len(masked.split())} words)")

        build = subprocess.run(
            [sys.executable, str(REPO / "tools" / "build_manuscript_pdf.py"),
             str(args.out_md), "--out", str(args.out_pdf)],
            cwd=REPO, capture_output=True, text=True,
        )
        sys.stdout.write(build.stdout)
        if build.returncode != 0:
            sys.stderr.write(build.stderr)
            return build.returncode

    leaked = verify(args.out_pdf)
    if leaked:
        print("\nMASKING FAILED. These identify the author and are still in the PDF:")
        for token in leaked:
            print(f"  - {token}")
        return 1
    print(f"\nmasking verified: none of {len(FORBIDDEN)} forbidden tokens is in "
          f"{args.out_pdf.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
