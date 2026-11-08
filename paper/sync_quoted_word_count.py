"""Set every quoted word count to the manuscript's actual one.

The count is resolved from the manuscript, never typed, because this is the figure the
cover letter asserts back to the editor and the one the kit's compliance table claims.

Two bugs in earlier versions of this script, both found by the gates it exists to serve:

1. It matched only four-digit numbers in the 4000s, so it reported "already correct"
   while the files carried 5009 -- a checker that could not see the value it checked.
2. It then excluded 5000 from the pattern, to avoid rewriting the *limit*. When the
   manuscript briefly stood at exactly 5000 words it wrote 5000 into the count cells,
   after which the same exclusion made those cells invisible to it. A pattern cannot
   tell a count from a limit by its value.

So the counts are now addressed by position: the kit's two table rows and the letters'
sentences are each matched by their surrounding text, and the limit is never touched.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAPER = Path(__file__).resolve().parent
actual = len((PAPER / "ctdose-core_jacmp_technote.md").read_text(encoding="utf-8").split())
print(f"manuscript is {actual} words")

NUM = r"\d{4}"

#: (file, pattern with one group for the count, description). The group is what changes;
#: everything else anchors it, so the 5000-word limit beside it is never rewritten.
TARGETS = [
    (
        "jacmp_submission_kit.md",
        rf"(\| Total word count, all elements \| 5000 \| ){NUM}( \| pass \|)",
        "the kit's compliance row",
    ),
    (
        "jacmp_submission_kit.md",
        rf"(\| Words, all elements \| 2972 \| ){NUM}( \|)",
        "the kit's change table",
    ),
    (
        "cover_letter_jacmp_r2.md",
        rf"(counting every element it runs to ){NUM}( words)",
        "the cover letter",
    ),
    (
        "response_to_editor_jacmp_r2.md",
        rf"(\| Manuscript, all elements counted \| 2972 words \| ){NUM}( words \(limit 5000\) \|)",
        "the response's change table",
    ),
    (
        "response_to_editor_jacmp_r2.md",
        rf"(It has grown from 2972 to ){NUM}( words)",
        "the response's summary",
    ),
]

changed = 0
for name, pattern, what in TARGETS:
    path = PAPER / name
    if not path.is_file():
        print(f"  {what}: {name} is not in this checkout")
        continue
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    if not match:
        print(f"  {what}: PATTERN DID NOT MATCH -- check {name} by hand")
        continue
    was = re.search(NUM, match.group(0)[len(match.group(1)):]).group(0)
    if int(was) == actual:
        print(f"  {what}: already {actual}")
        continue
    text = re.sub(pattern, rf"\g<1>{actual}\g<2>", text, count=1)
    path.write_text(text, encoding="utf-8", newline="")
    print(f"  {what}: {was} -> {actual}")
    changed += 1

# Verify: each target now reads the real count.
for name, pattern, what in TARGETS:
    path = PAPER / name
    if not path.is_file():
        continue
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    assert match, f"{what} no longer matches its own pattern"
    assert str(actual) in match.group(0), f"{what} does not state {actual}"
print(f"{changed} change(s); every quoted word count now matches the manuscript")
