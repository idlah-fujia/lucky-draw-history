"""The only winner fields this app stores or renders."""

from __future__ import annotations

FIELDS: tuple[tuple[str, str], ...] = (
    ("event", "Event"),
    ("id_firm", "ID Firm"),
    ("id_number", "ID Number"),
    ("homeowner", "Homeowner"),
    ("homeowner_number", "Homeowner Number"),
    ("item_won", "Item Won"),
)

FIELD_KEYS: tuple[str, ...] = tuple(key for key, _label in FIELDS)


def empty_row() -> dict[str, str]:
    return {key: "" for key in FIELD_KEYS}
