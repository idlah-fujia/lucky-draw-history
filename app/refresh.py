"""Build the unified lucky-draw rows in memory.

Sources, in order:
1. Pre-Renocode CSV (historical winners).
2. Live Renocode tables from Supabase: events, attendees,
   attendees_rf23_draft_backup, with blank fields filled from audit_log.
3. Google Form responses CSV.

The original workspace script was not available in this environment, so this
module follows that pipeline's described behaviour: merge, canonicalise firm
names, recover fields from audit_log, enrich designer phones, and dedupe.
It does not read or write /workspace. Row objects contain only the six
winner fields.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Mapping

import httpx

from app.crypto_util import CryptoError, decrypt_file
from app.fields import FIELD_KEYS, empty_row
from app.phones import normalize_phone, phone_tokens
from app.sb import SupabaseError, SupabaseRest

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

TABLES = ("events", "attendees", "attendees_rf23_draft_backup", "audit_log")
OPTIONAL_TABLES = {"attendees_rf23_draft_backup"}

# Longer hints are tried first so "homeowner contact" beats "homeowner".
_HINTS: tuple[tuple[str, str], ...] = (
    ("id_number", "designer contact number"),
    ("id_number", "designer phone"),
    ("id_number", "designer number"),
    ("id_number", "designer mobile"),
    ("id_number", "id phone"),
    ("id_number", "id number"),
    ("homeowner_number", "homeowner contact"),
    ("homeowner_number", "home owner contact"),
    ("homeowner_number", "homeowner number"),
    ("homeowner_number", "home owner number"),
    ("homeowner_number", "homeowner phone"),
    ("homeowner_number", "home owner phone"),
    ("homeowner_number", "customer phone"),
    ("homeowner_number", "mobile number"),
    ("homeowner_number", "contact number"),
    ("homeowner", "homeowner name"),
    ("homeowner", "home owner name"),
    ("homeowner", "customer name"),
    ("homeowner", "visitor name"),
    ("homeowner", "homeowner"),
    ("homeowner", "home owner"),
    ("id_firm", "designer firm"),
    ("id_firm", "id firm"),
    ("id_firm", "id company"),
    ("id_firm", "firm name"),
    ("id_firm", "company name"),
    ("id_firm", "firm"),
    ("id_firm", "company"),
    ("item_won", "item won"),
    ("item_won", "lucky draw prize"),
    ("item_won", "prize name"),
    ("item_won", "prize"),
    ("item_won", "gift"),
    ("item_won", "item"),
    ("event", "event name"),
    ("event", "which event"),
    ("event", "event"),
)

_FALSE = {False, 0, "0", "false", "no"}
_SPACE = re.compile(r"\s+")


def _clean(value: Any) -> str:
    if value is None or value is False:
        return ""
    return _SPACE.sub(" ", str(value)).strip()


def _hint_in(hint: str, header: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(hint)}(?![a-z0-9])", header) is not None


def _norm_header(header: str) -> str:
    return _SPACE.sub(" ", header.lower().replace("_", " ").replace("-", " ")).strip()


def assign_headers(headers: list[str]) -> dict[str, str]:
    """Map a field key to the original header string."""
    prepared = [(header, _norm_header(header)) for header in headers if header]
    hints = sorted(_HINTS, key=lambda item: len(item[1]), reverse=True)
    assigned: dict[str, str] = {}
    used: set[str] = set()
    for field, hint in hints:
        if field in assigned:
            continue
        for original, normalised in prepared:
            if original in used:
                continue
            if _hint_in(hint, normalised):
                assigned[field] = original
                used.add(original)
                break
    if "homeowner" not in assigned:
        for original, normalised in prepared:
            if original not in used and normalised in {"name", "full name", "your name"}:
                assigned["homeowner"] = original
                used.add(original)
                break
    if "homeowner_number" not in assigned:
        for original, normalised in prepared:
            if original not in used and normalised == "phone":
                assigned["homeowner_number"] = original
                break
    return assigned


def _row_from_mapping(record: Mapping[str, Any], assigned: dict[str, str]) -> dict[str, str]:
    row = empty_row()
    for field, header in assigned.items():
        row[field] = _clean(record.get(header))
    return row


def parse_csv(text: str) -> list[dict[str, str]]:
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return []
    first = text.splitlines()[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        return []
    assigned = assign_headers(list(reader.fieldnames))
    rows: list[dict[str, str]] = []
    for record in reader:
        row = _row_from_mapping(record, assigned)
        if any(row.values()):
            rows.append(row)
    return rows


def _looks_like_id(value: str) -> bool:
    if value.isdigit():
        return True
    return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{16,}", value.lower()))


def _display_keys(record: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in record:
        low = str(key).lower()
        if low == "id" or low.endswith("_id"):
            continue
        keys.append(str(key))
    return keys


def index_events(events: list[Mapping[str, Any]]) -> dict[str, str]:
    by_id: dict[str, str] = {}
    for event in events:
        assigned = assign_headers(_display_keys(event))
        label = ""
        if "event" in assigned:
            label = _clean(event.get(assigned["event"]))
        if not label:
            for key in ("name", "title", "label", "code"):
                label = _clean(event.get(key))
                if label:
                    break
        if not label:
            continue
        for key in ("id", "event_id", "code"):
            raw = event.get(key)
            if raw not in (None, ""):
                by_id[str(raw)] = label
    return by_id


def _explicitly_not_winner(record: Mapping[str, Any]) -> bool:
    for key in ("is_winner", "won", "is_lucky_draw_winner"):
        if key in record and record[key] in _FALSE:
            return True
    return False


def index_audit(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("created_at") or row.get("timestamp") or row.get("id") or ""))
    index: dict[str, dict[str, Any]] = {}
    for row in ordered:
        table = str(row.get("table") or row.get("table_name") or "")
        if table and "attendee" not in table.lower():
            continue
        record_id = row.get("attendee_id") or row.get("record_id") or row.get("row_id")
        if record_id in (None, ""):
            continue
        changes: dict[str, Any] = {}
        for container_key in ("changed_fields", "payload", "changes"):
            container = row.get(container_key)
            if isinstance(container, str):
                try:
                    container = json.loads(container)
                except json.JSONDecodeError:
                    container = None
            if isinstance(container, dict):
                changes.update(container)
        field = row.get("field") or row.get("column") or row.get("column_name")
        value = row.get("new_value")
        if value in (None, ""):
            value = row.get("value")
        if field not in (None, "") and value not in (None, ""):
            changes[str(field)] = value
        slot = index.setdefault(str(record_id), {})
        for key, val in changes.items():
            if val not in (None, ""):
                slot[str(key)] = val
    return index


def _event_label(record: Mapping[str, Any], events_by_id: dict[str, str]) -> str:
    event_id = record.get("event_id")
    if event_id not in (None, "") and str(event_id) in events_by_id:
        return events_by_id[str(event_id)]
    assigned = assign_headers(_display_keys(record))
    raw = _clean(record.get(assigned["event"])) if "event" in assigned else ""
    if raw and _looks_like_id(raw) and raw in events_by_id:
        return events_by_id[raw]
    return raw


def _fill_blanks(row: dict[str, str], extra: Mapping[str, Any]) -> None:
    if not extra:
        return
    assigned = assign_headers([key for key in extra if not str(key).lower().endswith("_id")])
    for field, header in assigned.items():
        if not row[field]:
            row[field] = _clean(extra.get(header))


def row_from_attendee(
    record: Mapping[str, Any],
    events_by_id: dict[str, str],
    audit: dict[str, dict[str, Any]],
) -> dict[str, str] | None:
    if _explicitly_not_winner(record):
        return None
    assigned = assign_headers(_display_keys(record))
    row = _row_from_mapping(record, assigned)
    row["event"] = _event_label(record, events_by_id) or row["event"]
    record_id = record.get("id")
    if record_id not in (None, ""):
        _fill_blanks(row, audit.get(str(record_id), {}))
    designer = _clean(record.get("designer_name") or record.get("designer") or record.get("id_name"))
    if designer:
        row["_designer"] = designer
    if not any(row.get(key) for key in FIELD_KEYS):
        return None
    return row


def _canon_key(key: str) -> str:
    number = normalize_phone(key)
    if number:
        return number
    return _SPACE.sub(" ", key).strip().casefold()


def normalize_constants(raw: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    raw = raw or {}

    def block(name: str) -> dict[str, str]:
        value = raw.get(name) or {}
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            cleaned_key = _canon_key(str(key))
            if cleaned_key:
                out[cleaned_key] = _clean(item)
        return out

    return {
        "VERIFIED_TYPO_CANONICAL": block("VERIFIED_TYPO_CANONICAL"),
        "WHATSAPP_KNOWN_NUMBERS": block("WHATSAPP_KNOWN_NUMBERS"),
        "FIRM_ALIASES": block("FIRM_ALIASES"),
    }


def _rewrite_phones(value: str, typos: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        number = normalize_phone(match.group(0))
        if number and number in typos:
            return typos[number]
        return match.group(0)

    return re.sub(r"\+?\d[\d\s+\-]{5,}\d", repl, value)


def apply_constants(row: dict[str, str], constants: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    out = {key: _clean(row.get(key, "")) for key in FIELD_KEYS}
    aliases = constants.get("FIRM_ALIASES") or {}
    typos = constants.get("VERIFIED_TYPO_CANONICAL") or {}
    known = constants.get("WHATSAPP_KNOWN_NUMBERS") or {}

    firm_key = out["id_firm"].casefold()
    if firm_key in aliases:
        out["id_firm"] = aliases[firm_key]
        firm_key = out["id_firm"].casefold()
    if firm_key in typos and not normalize_phone(firm_key):
        out["id_firm"] = typos[firm_key]
        firm_key = out["id_firm"].casefold()

    out["id_number"] = _rewrite_phones(out["id_number"], typos)
    out["homeowner_number"] = _rewrite_phones(out["homeowner_number"], typos)

    if not phone_tokens(out["id_number"]):
        designer = _clean(row.get("_designer", "")).casefold()
        person = out["homeowner"].casefold()
        found = ""
        for key in (
            f"{firm_key}|{designer}" if designer else "",
            designer,
            f"{firm_key}|{person}" if person else "",
            person,
            firm_key,
        ):
            if key and known.get(key):
                found = known[key]
                break
        if found:
            out["id_number"] = found
    return out


def _dedupe_key(row: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(_SPACE.sub(" ", row[key]).strip().casefold() for key in FIELD_KEYS)


def build_rows(
    *,
    pre_csv: str,
    form_csv: str,
    tables: Mapping[str, list[Mapping[str, Any]]] | None,
    constants: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Merge the three sources and return one dict per win."""
    canon = normalize_constants(constants)
    tables = tables or {}
    events = index_events(list(tables.get("events") or []))
    audit = index_audit(list(tables.get("audit_log") or []))
    combined: list[dict[str, str]] = []
    combined.extend(parse_csv(pre_csv))
    for table in ("attendees", "attendees_rf23_draft_backup"):
        for record in tables.get(table) or []:
            row = row_from_attendee(record, events, audit)
            if row:
                combined.append(row)
    combined.extend(parse_csv(form_csv))

    seen: set[tuple[str, ...]] = set()
    result: list[dict[str, str]] = []
    for row in combined:
        cleaned = apply_constants(row, canon)
        if not any(cleaned.values()):
            continue
        key = _dedupe_key(cleaned)
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _read_secret_text(path: Path, key_b64: str, *, required_when_present: bool) -> str:
    if not path.exists():
        logger.warning("encrypted file missing: %s", path.name)
        return ""
    if not key_b64:
        message = f"{path.name} is present but DATA_KEY is not set"
        if required_when_present:
            raise CryptoError(message)
        logger.warning(message)
        return ""
    return decrypt_file(path, key_b64).decode("utf-8")


def load_constants(path: Path, key_b64: str) -> dict[str, dict[str, str]]:
    if not path.exists():
        logger.info("designer constants file missing; using empty maps")
        return normalize_constants({})
    if not key_b64:
        raise CryptoError(f"{path.name} is present but DATA_KEY is not set")
    payload = json.loads(decrypt_file(path, key_b64).decode("utf-8"))
    if not isinstance(payload, dict):
        raise CryptoError("designer constants must be a JSON object")
    return normalize_constants(payload)


def fetch_tables(url: str, service_role_key: str) -> dict[str, list[dict[str, Any]]]:
    if not url or not service_role_key:
        logger.warning("Supabase is not configured; live tables are empty")
        return {name: [] for name in TABLES}
    with httpx.Client(timeout=60.0) as client:
        api = SupabaseRest(url, service_role_key, client)
        tables: dict[str, list[dict[str, Any]]] = {}
        for name in TABLES:
            try:
                tables[name] = api.get_all(name)
            except SupabaseError as exc:
                if name in OPTIONAL_TABLES and exc.status == 404:
                    logger.warning("optional supabase table missing: %s", name)
                    tables[name] = []
                    continue
                raise
        return tables


def load_all() -> list[dict[str, str]]:
    """Decrypt local files, pull Supabase, and return winner rows."""
    data_dir = Path(os.environ.get("LUCKY_DRAW_DATA_DIR", DATA_DIR))
    key = os.environ.get("DATA_KEY", "").strip()
    pre = _read_secret_text(data_dir / "pre_renocode.csv.enc.b64.txt", key, required_when_present=True)
    form = _read_secret_text(data_dir / "form_responses.csv.enc.b64.txt", key, required_when_present=True)
    constants = load_constants(data_dir / "designer_constants.json.enc.b64.txt", key)
    tables = fetch_tables(
        os.environ.get("SUPABASE_URL", "").strip(),
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
    )
    rows = build_rows(pre_csv=pre, form_csv=form, tables=tables, constants=constants)
    logger.info("built winner rows count=%s", len(rows))
    return rows
