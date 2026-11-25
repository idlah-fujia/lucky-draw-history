"""Singapore mobile normalisation for lucky-draw lookups.

Staff type numbers with spaces, dashes, or a leading +65. Stored cells sometimes
hold two numbers separated by a slash. Lookup input must become one 8-digit
number. A stored cell matches when any 8-digit number inside it matches.
"""

from __future__ import annotations

import re

_STRIP = re.compile(r"[\s+\-]")
_CHUNKS = re.compile(r"\d+")


def normalize_phone(raw: str | None) -> str | None:
    """Return an 8-digit number, or None when the whole value is not one."""
    if raw is None:
        return None
    digits = _STRIP.sub("", str(raw))
    if not digits.isdigit():
        return None
    if len(digits) == 10 and digits.startswith("65"):
        digits = digits[2:]
    if len(digits) == 8:
        return digits
    return None


def phone_tokens(raw: str | None) -> set[str]:
    """Every 8-digit number inside a cell, after the same stripping rules."""
    if raw is None or raw == "":
        return set()
    compact = _STRIP.sub("", str(raw))
    found: set[str] = set()
    for chunk in _CHUNKS.findall(compact):
        if len(chunk) == 10 and chunk.startswith("65"):
            chunk = chunk[2:]
        if len(chunk) == 8:
            found.add(chunk)
    return found
