"""Address normalization (binding-scope item 14).

Brief lines 86 to 92 specify:
- lowercase
- trim leading/trailing whitespace
- collapse internal whitespace runs
- strip apt/unit suffix via regex `(apt|unit|#)\\s*\\w+\\s*$`
- expand abbreviations: st, rd, dr, ln, ct, cir, ave, blvd, pl
- "way" stays as "way" (NOT abbreviated)

Pure function (no DB). Matched-against-DB happens in services/address_store.py.

Examples:
  "123 Main St"               -> "123 main street"
  "  123  Main  St.   "       -> "123 main street"
  "456 OAK RD APT 2B"         -> "456 oak road"
  "789 Pine Way"              -> "789 pine way"   (way is preserved)
  "100 Elm Blvd #4"           -> "100 elm boulevard"
"""

from __future__ import annotations

import re

# Suffix abbreviations the brief specifies. "way" is intentionally NOT here.
ABBREVIATIONS: dict[str, str] = {
    "st": "street",
    "rd": "road",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "cir": "circle",
    "ave": "avenue",
    "blvd": "boulevard",
    "pl": "place",
}

# Trailing apt/unit/# suffix. Matches "apt 2", "unit 3b", "# 4", optional period.
APT_UNIT_PATTERN = re.compile(r"(apt|unit|#)\.?\s*\w+\s*$", re.IGNORECASE)

# Periods after abbreviations like "St." -> drop them so the abbreviation map matches.
_TRAILING_PERIOD = re.compile(r"\.")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize(raw: str) -> str:
    """Apply the address normalization pipeline.

    Steps in order:
    1. lowercase
    2. drop periods (so "St." matches "st")
    3. strip apt/unit/# suffix
    4. collapse whitespace
    5. expand abbreviations on every token

    Returns the normalized string. Empty input returns empty string.
    """
    if raw is None:
        return ""
    s = raw.strip().lower()
    if not s:
        return ""
    s = _TRAILING_PERIOD.sub("", s)
    s = APT_UNIT_PATTERN.sub("", s).strip()
    s = _WHITESPACE_RUN.sub(" ", s)
    tokens = s.split(" ")
    expanded = [ABBREVIATIONS.get(tok, tok) for tok in tokens]
    return " ".join(expanded).strip()
