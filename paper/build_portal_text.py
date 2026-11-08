"""Generate the plain text that gets pasted into the submission form.

    python paper/build_portal_text.py

Two of the things a journal asks for are *typed into a form*, not uploaded: the abstract
box and the cover-letter box. Neither is a file, so no build reaches them and no gate that
reads files can see them. On IORN-007 that cost a round: two of five Novel Scientific
Points went to the journal carrying claims the manuscript had withdrawn four hours
earlier, because the manuscript was rebuilt and the typed field was not.

So the pasteable text is built, like everything else, and then pinned by
``paper/record_portal_fields.py`` once it has been pasted.

Two traps this closes for the JACMP submission:

* **The wrong abstract is sitting in the repository.** ``abstract_masked_structured.txt``
  is the *Research Article's* abstract, 491 words. The Technical Note's is 344. That file
  is correct for the manuscript it belongs to and is checked against it, but it is the
  one an author reaches for at the portal, and pasting it would put a superseded
  500-word abstract on a Technical Note.
* **Markdown is not plain text.** The cover letter is written in Markdown and the box
  takes none of it. The runbook's rule is one paragraph per line with a blank line
  between, because a hard-wrapped letter arrives with every line broken.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAPER = Path(__file__).resolve().parent
BUILD = PAPER / "build"

#: The masked Technical Note, because the review is double-masked and the abstract box is
#: review-facing. Its abstract is the one this submission carries.
MASKED = PAPER / "ctdose-core_jacmp_technote_masked.md"
COVER = PAPER / "cover_letter_jacmp_r2.md"

ABSTRACT_OUT = BUILD / "abstract_for_portal.txt"
COVER_OUT = BUILD / "cover_letter_for_portal.txt"


def strip_markdown(text: str) -> str:
    """Plain text for a textarea: no emphasis marks, no links, no headings."""
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text


def one_paragraph_per_line(text: str) -> str:
    """Join each paragraph onto one line, blank line between.

    The runbook's rule for a textarea. A letter hard-wrapped at 90 columns arrives with
    every line broken, because the box wraps for itself.
    """
    paragraphs = re.split(r"\n\s*\n", text.strip())
    out = []
    for para in paragraphs:
        joined = " ".join(line.strip() for line in para.splitlines() if line.strip())
        if joined:
            out.append(joined)
    return "\n\n".join(out) + "\n"


def abstract_text() -> str:
    body = MASKED.read_text(encoding="utf-8")
    start = body.index("## Abstract")
    end = body.index("\n---\n", start)
    abstract = body[start:end].replace("## Abstract", "", 1)
    return one_paragraph_per_line(strip_markdown(abstract))


def cover_text() -> str:
    body = COVER.read_text(encoding="utf-8")
    # The address block at the top is the letterhead; the box wants the letter.
    marker = "To the Editors"
    if marker in body:
        body = body[body.index(marker) :]
    return one_paragraph_per_line(strip_markdown(body))


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    for path, text, label in (
        (ABSTRACT_OUT, abstract_text(), "abstract box"),
        (COVER_OUT, cover_text(), "cover-letter box"),
    ):
        path.write_text(text, encoding="utf-8", newline="\n")
        longest = max((len(line) for line in text.splitlines()), default=0)
        print(
            f"  wrote {path.name:34} {len(text.split()):4d} words, "
            f"{len(text.splitlines()):2d} lines, longest {longest}  ({label})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
