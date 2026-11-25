"""In-memory port of the original lucky-draw refresh script.

The row rules match that script: pre-Renocode CSV, live Renocode tables
(events, attendees, attendees_rf23_draft_backup, audit_log), and the Google
Form CSV. Phone maps that were hard-coded in the original are loaded from
the encrypted designer-constants file and are empty when that file is absent.
Nothing is written to disk.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from app import sb
from app.crypto_util import CryptoError, decrypt_file

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

GetFn = Callable[..., list]

# Canonical firm mapping (case-insensitive regex -> display name)
FIRM_RULES = [
    (r"vii|vll", "VII LUXE"),
    (r"central", "CDG Central"),
    (r"conquest|cdg", "Conquest Design Group"),
    (r"^2j", "2JS"),
    (r"supreme", "Supreme Houzz"),
    (r"elys", "Elysian Design"),
    (r"intern|interino", "Internio Design"),
    (r"empyrean", "Empyrean"),
    (r"interior d(ia|ai)ry", "Interior Diary"),
    (r"reborn", "Reborn Interior"),
    (r"^proz|^pro\s?design", "Proz Design"),
    (r"ciseern|cissern", "Ciseern"),
    (r"sora", "Sora Studios"),
    (r"brick", "Brick & Deco"),
    (r"^i[\s-]?interior|^22\s*interior", "I-Interior"),
    (r"inori", "Inori"),
    (r"chapter\s?13", "Chapter 13"),
    (r"quad", "Quad R Design"),
    (r"va\s*maison", "VA Maison"),
    (r"onyx", "Onyx & Co"),
    (r"foresta|forest\s*lab", "Foresta Lab"),
    (r"id\.?eology|ideology", "ID.EOLOGY"),
    (r"fyner", "Fyner Studio"),
    (r"erahauz", "Erahauz"),
    (r"concrid", "Concrid Interior"),
    (r"woodcraft", "Woodcraft Interior"),
    (r"june\s*interior|^june$", "June Interior"),
    (r"u-?home", "U-Home"),
    (r"mydesign", "MYDesign"),
    (r"lifesize", "Lifesize Plans"),
    (r"houzz\s*reno", "Houzz Reno"),
]

EVENT_LABELS = {
    "RF32": "RF32 (14–15 Nov 2026)",
    "RF31": "RF31 (31 Oct–1 Nov 2026)",
    "RF31V": "RF31V Virtual (31 Oct–1 Nov 2026)",
    "RF30": "RF30 (16–18 Oct 2026)",
    "RF29": "RF29 (10–11 Oct 2026)",
    "RF28": "RF28 (26–27 Sep 2026)",
    "HF2": "HF2 (18–20 Sep 2026)",
    "RF27": "RF27 (5–6 Sep 2026)",
    "RF26": "RF26 (22–23 Aug 2026)",
    "RF25": "RF25 (14–16 Aug 2026)",
    "RF24": "RF24 (1–2 Aug 2026)",
    "HF1": "HF1 (24–26 Jul 2026)",
    "RF23": "RF23 (11–12 Jul 2026)",
    "RF22": "RF22 (27–28 Jun 2026)",
    "RF21": "RF21 (6–7 Jun 2026)",
    "RF20": "RF20 (23–24 May 2026)",
    "RF19": "RF19 (16–17 May 2026)",
    "RF18": "RF18 (9–10 May 2026)",
    "RF17": "RF17 (25–28 Apr 2026)",
    "RF16": "RF16 (10–12 Apr 2026)",
    "RF15": "RF15 (28–29 Mar 2026)",
    "RF14": "RF14 (7–8 Mar 2026)",
    "RF13": "RF13 (24–25 Jan 2026)",
    "RF12": "RF12 (10–11 Jan 2026)",
    "DREAMSCAPE": "Dreamscape (Dec 2025)",
    "RF11": "RF11 (15–16 Nov 2025)",
    "RF10": "RF10 (1–2 Nov 2025)",
    "RF9": "RF9 (25–26 Oct 2025)",
    "RF8": "RF8 (12 Oct 2025)",
    "PRE_2025_09_27": "Pre-RF8 (27–28 Sep 2025)",
    "PRE_2025_09_20": "Pre-RF8 (20–21 Sep 2025)",
    "PRE_2025_09_13": "Pre-RF8 (13 Sep 2025)",
    "PRE_2025_08_23": "Pre-RF8 (23–24 Aug 2025)",
    "PRE_UNKNOWN": "Unspecified Event",
}

EVENT_ORDER = list(EVENT_LABELS.keys())

ATTENDEE_SELECT = (
    "attendees?select=id,event_id,name,phone,lucky_draw,lucky_draw_enrolled"
    "&or=(lucky_draw.not.is.null,lucky_draw_enrolled.eq.true)"
)
BACKUP_SELECT = (
    "attendees_rf23_draft_backup?select=id,event_id,name,phone,lucky_draw,lucky_draw_enrolled"
    "&or=(lucky_draw.not.is.null,lucky_draw_enrolled.eq.true)"
)
AUDIT_SELECT = "audit_log?action=eq.lucky_draw&select=event_id,attendee_id,detail,created_at&order=created_at.asc"


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"[\u202a\u202c\u200e\u2068\u2069\u2060\xa0]", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()


def canon_firm(value: Any) -> str:
    lowered = clean_str(value).lower()
    if not lowered:
        return ""
    for pattern, name in FIRM_RULES:
        if re.search(pattern, lowered):
            return name
    return clean_str(value)


def extract_sg_phones(value: Any) -> list[str]:
    text = clean_str(value)
    found = re.findall(r"(?:\+?65[\s\-]*)?([3689]\d{3}[\s\-]?\d{4})", text)
    out: list[str] = []
    for item in found:
        digits = re.sub(r"\D", "", item)
        if len(digits) == 8 and digits not in out:
            out.append(digits)
    return out


def format_phone_full(raw: Any, fallback_sg_list: list[str] | None = None) -> str:
    """Preserve the full phone number without masking."""
    text = clean_str(raw)
    if not text:
        return " / ".join(fallback_sg_list) if fallback_sg_list else ""
    sg = extract_sg_phones(text)
    if sg:
        return " / ".join(sg)
    digits = re.sub(r"[^\d+]", "", text)
    if digits:
        if text.startswith("+"):
            return text
        if text.startswith("44") or text.startswith("60"):
            return "+" + text
        return text
    return text


def norm_designer_key(value: Any) -> str:
    text = clean_str(value).lower()
    text = re.sub(r"\(.*?\)", "", text).strip()
    return re.sub(r"\s+", " ", text)


def firm_group(firm: str) -> str:
    if firm in ("CDG Central", "Conquest Design Group"):
        return "Conquest"
    if firm in ("Houzz Reno", "Supreme Houzz"):
        return "Supreme Houzz"
    return firm


def map_form_event(ev_str: Any, ts_str: Any) -> str:
    el = clean_str(ev_str).lower()
    ts = clean_str(ts_str)
    match = re.search(r"\brf\s?(\d{1,2})\b", el)
    if match:
        return f"RF{int(match.group(1))}"
    if "dreamscape" in el:
        return "DREAMSCAPE"
    if "15/11/2026" in el or "nova furnishing" in el:
        return "RF32"
    if "18 oct" in el or "17/10" in el or "nova idlah" in el:
        return "RF30"
    if "25 july 2026" in el or "suntec city" in el:
        return "HF1"
    if "6/9/26" in el or "62 changi" in el:
        return "RF27"
    if "16/5/26" in el or "17/05/26" in el:
        return "RF19"
    if "10 may 2026" in el:
        return "RF18"
    if "25-04-2026" in el or "26 april 2026" in el:
        return "RF17"
    if "11 april 2026" in el:
        return "RF16"
    if "24/1/26" in el or "24/01/2026" in el or "24jan" in el or "25/1/2026" in el or "jan soon bee huat" in el:
        return "RF13"
    if "12 jan 2026" in el or "10/1/26" in el:
        return "RF12"
    if "16 november 2025" in el:
        return "RF11"
    if "2nd november" in el:
        return "RF10"
    if "11 october 2025" in el or "11-10-2025" in el or "11/10/" in el:
        return "RF8"
    if "27/9/2025" in el or "28 september 2025" in el or "28/9" in el:
        return "PRE_2025_09_27"
    if "20/21 sept" in el or "20th sept" in el or "20 september" in el or "20 sep" in el:
        return "PRE_2025_09_20"
    if "13th sept" in el or "13 sep" in el:
        return "PRE_2025_09_13"
    if "23-24 august" in el:
        return "PRE_2025_08_23"
    if el == "idlah" and ts.startswith("18/10/2025"):
        return "PRE_UNKNOWN"
    return "PRE_UNKNOWN"


def fetch_all_sb(sb_get: GetFn, path: str) -> list:
    rows: list = []
    start = 0
    while True:
        batch = sb_get(path, rng=f"{start}-{start + 999}")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start += 1000
    return rows


def _pair_map(block: Any) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    if not isinstance(block, dict):
        return out
    for key, phone in block.items():
        if isinstance(key, tuple) and len(key) == 2:
            firm, designer = str(key[0]), str(key[1])
        else:
            if str(key).startswith("_"):
                continue
            firm, sep, designer = str(key).partition("|")
            if not sep or not firm or not designer:
                raise ValueError("phone maps must use firm|designer keys")
        out[(firm, designer)] = "" if phone is None else str(phone)
    return out


def normalize_constants(raw: dict | None) -> dict:
    raw = raw or {}
    spouse_raw = raw.get("FORM_SPOUSE_MATCH_TO_EXISTING") or {}
    spouse: dict[int, tuple[str, str, str, str]] = {}
    if isinstance(spouse_raw, dict):
        items = spouse_raw.items()
    else:
        raise ValueError("FORM_SPOUSE_MATCH_TO_EXISTING must be an object")
    for key, target in items:
        if str(key).startswith("_"):
            continue
        if not isinstance(target, (list, tuple)) or len(target) != 4:
            raise ValueError("each spouse match needs source, event, firm, and phone")
        spouse[int(key)] = (str(target[0]), str(target[1]), str(target[2]), str(target[3]))
    return {
        "VERIFIED_TYPO_CANONICAL": _pair_map(raw.get("VERIFIED_TYPO_CANONICAL")),
        "WHATSAPP_KNOWN_NUMBERS": _pair_map(raw.get("WHATSAPP_KNOWN_NUMBERS")),
        "FORM_SPOUSE_MATCH_TO_EXISTING": spouse,
    }


def _read_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text or "")))


def build_rows(pre_csv: str, form_csv: str, sb_get: GetFn, constants: dict | None = None) -> list[dict[str, str]]:
    """Return winner rows in the original order. Each row has the six public fields."""
    canon = normalize_constants(constants)
    verified = canon["VERIFIED_TYPO_CANONICAL"]
    whatsapp = canon["WHATSAPP_KNOWN_NUMBERS"]
    spouse_matches = canon["FORM_SPOUSE_MATCH_TO_EXISTING"]

    pre_raw = _read_csv(pre_csv)
    form_raw = _read_csv(form_csv)

    events_list = sb_get("events?select=*")
    events_by_id = {event["id"]: event for event in events_list}
    rc_att = fetch_all_sb(sb_get, ATTENDEE_SELECT)
    rc_att_b23 = fetch_all_sb(sb_get, BACKUP_SELECT)
    rc_logs = fetch_all_sb(sb_get, AUDIT_SELECT)
    logs_by_aid: dict[tuple, list] = defaultdict(list)
    for log in rc_logs:
        logs_by_aid[(log["event_id"], log["attendee_id"])].append(log)

    form_by_phone: dict[str, list] = defaultdict(list)
    for idx, form_row in enumerate(form_raw, 1):
        for phone in extract_sg_phones(form_row["Homeowner Number"]):
            form_by_phone[phone].append((idx, form_row))

    designer_phone_counts: dict[tuple, Counter] = defaultdict(Counter)
    designer_first_counts: dict[tuple, Counter] = defaultdict(Counter)

    def add_designer_obs(firm_canon: str, designer_raw: Any, phone_raw: Any) -> None:
        phones = extract_sg_phones(phone_raw)
        if not firm_canon or not designer_raw or len(phones) != 1:
            return
        phone = phones[0]
        designer_key = norm_designer_key(designer_raw)
        if not designer_key or "/" in designer_key or "&" in designer_key:
            return
        group = firm_group(firm_canon)
        for firm_key in {firm_canon, group}:
            designer_phone_counts[(firm_key, designer_key)][phone] += 1
            first = designer_key.split()[0]
            if len(first) >= 3:
                designer_first_counts[(firm_key, first)][(designer_key, phone)] += 1

    for row in pre_raw:
        add_designer_obs(
            canon_firm(row["firm"] or row["firm_raw"]),
            row["designer"],
            row["designer_phone"] or row["designer_phone_raw"],
        )
    for form_row in form_raw:
        add_designer_obs(
            canon_firm(form_row["Interior Design Company"]),
            form_row["Designer Name"],
            form_row["Designer Number"],
        )

    def lookup_designer_phone(firm_canon: str, designer_raw: Any) -> str:
        designer_key = norm_designer_key(designer_raw)
        if not firm_canon or not designer_key:
            return ""
        if (firm_canon, designer_key) in verified:
            return verified[(firm_canon, designer_key)]
        if (firm_canon, designer_key) in whatsapp:
            return whatsapp[(firm_canon, designer_key)]
        for firm_key in (firm_canon, firm_group(firm_canon)):
            counts = designer_phone_counts.get((firm_key, designer_key))
            if counts and len(counts) == 1:
                return next(iter(counts))
        if " " not in designer_key and len(designer_key) >= 3:
            for firm_key in (firm_canon, firm_group(firm_canon)):
                first_counts = designer_first_counts.get((firm_key, designer_key))
                if first_counts:
                    phones = {phone for (_, phone) in first_counts}
                    if len(phones) == 1:
                        return next(iter(phones))
        return ""

    pre_rf22_by_phone = {}
    for row in pre_raw:
        if row["event_code"] == "RF22":
            for phone in extract_sg_phones(row["homeowner_phone"] or row["homeowner_phone_raw"]):
                pre_rf22_by_phone[phone] = row

    matched_form_indices: set[int] = set()
    entries: list[dict[str, str]] = []

    def try_enrich_from_form(ho_phones, firm_canon, designer_cur, id_num_cur, target_idx=None):
        candidates = []
        if target_idx is not None:
            candidates = [(target_idx, form_raw[target_idx - 1])]
        else:
            for phone in ho_phones:
                for idx, form_row in form_by_phone.get(phone, []):
                    form_firm = canon_firm(form_row["Interior Design Company"])
                    if not firm_canon or firm_group(form_firm) == firm_group(firm_canon) or not form_firm:
                        candidates.append((idx, form_row))
        for idx, form_row in candidates:
            matched_form_indices.add(idx)
            if not designer_cur and clean_str(form_row["Designer Name"]):
                designer_cur = clean_str(form_row["Designer Name"])
            if not id_num_cur:
                form_phones = extract_sg_phones(form_row["Designer Number"])
                if form_phones:
                    id_num_cur = " / ".join(form_phones)
        return designer_cur, id_num_cur

    rc_rf22_phones: set[str] = set()
    for row in rc_att:
        event = events_by_id.get(row["event_id"], {})
        if event.get("code") == "RF22" and isinstance(row.get("lucky_draw"), list):
            if any(
                isinstance(item, dict) and any(clean_str(item.get(key)) for key in ("firm", "prize", "designer"))
                for item in row["lucky_draw"]
            ):
                rc_rf22_phones.update(extract_sg_phones(row.get("phone")))

    for row in pre_raw:
        event_code = row["event_code"]
        ho_phones = extract_sg_phones(row["homeowner_phone"] or row["homeowner_phone_raw"])
        if event_code == "RF22" and any(phone in rc_rf22_phones for phone in ho_phones):
            continue

        firm = canon_firm(row["firm"] or row["firm_raw"])
        designer = clean_str(row["designer"])
        id_num = format_phone_full(row["designer_phone"] or row["designer_phone_raw"])
        if not id_num:
            id_num = lookup_designer_phone(firm, designer)

        forced = None
        for idx, target in spouse_matches.items():
            if target[0] == "PRE" and target[1] == event_code and target[3] in ho_phones:
                forced = idx
        designer, id_num = try_enrich_from_form(ho_phones, firm, designer, id_num, target_idx=forced)

        entries.append({
            "event_code": event_code,
            "event": EVENT_LABELS.get(event_code, event_code),
            "firm": firm,
            "id_number": id_num,
            "homeowner": clean_str(row["homeowner_name"]),
            "homeowner_number": format_phone_full(row["homeowner_phone_raw"] or row["homeowner_phone"], ho_phones),
            "item_won": clean_str(row["prize"]),
            "_source": f"pre:{row['entry_id']}",
            "_designer_internal": designer,
        })

    for is_backup, row in [(False, item) for item in rc_att] + [(True, item) for item in rc_att_b23]:
        event_obj = events_by_id.get(row["event_id"], {})
        event_code = "RF23" if is_backup else event_obj.get("code", "")
        lucky_draw = row.get("lucky_draw")
        if not isinstance(lucky_draw, list):
            continue

        ho_name = clean_str(row.get("name"))
        ho_phones = extract_sg_phones(row.get("phone"))
        ho_num = format_phone_full(row.get("phone"), ho_phones)

        for item_idx, item in enumerate(lucky_draw):
            if not isinstance(item, dict):
                continue
            firm_raw = clean_str(item.get("firm"))
            designer = clean_str(item.get("designer"))
            prize = clean_str(item.get("prize"))
            if not any((firm_raw, designer, prize)):
                continue
            if event_code == "RF20" and "aaishah" in ho_name.lower() and prize.lower() == "lkio":
                continue
            if "clara (rf testing)" in ho_name.lower() and not prize:
                continue
            if "lynn chiam" in ho_name.lower() and not prize and not designer:
                continue

            if not is_backup:
                for log in logs_by_aid.get((row["event_id"], row["id"]), []):
                    logged = (log.get("detail") or {}).get("lucky_draw")
                    if isinstance(logged, list) and item_idx < len(logged) and isinstance(logged[item_idx], dict):
                        if not firm_raw:
                            candidate = clean_str(logged[item_idx].get("firm"))
                            if candidate:
                                firm_raw = candidate
                        if not prize:
                            candidate = clean_str(logged[item_idx].get("prize"))
                            if candidate:
                                prize = candidate
                        if not designer:
                            candidate = clean_str(logged[item_idx].get("designer"))
                            if candidate and candidate != "I":
                                designer = candidate

            firm = canon_firm(firm_raw)
            id_num = ""
            if event_code == "RF22":
                for phone in ho_phones:
                    if phone in pre_rf22_by_phone:
                        previous = pre_rf22_by_phone[phone]
                        id_num = format_phone_full(previous["designer_phone"] or previous["designer_phone_raw"])
                        if not designer:
                            designer = clean_str(previous["designer"])

            forced = None
            for idx, target in spouse_matches.items():
                if target[0] == "RC" and target[1] == event_code and target[3] in ho_phones:
                    forced = idx
            designer, id_num = try_enrich_from_form(ho_phones, firm, designer, id_num, target_idx=forced)
            if not id_num:
                id_num = lookup_designer_phone(firm, designer)

            if event_code == "RF25" and "tiffany" in ho_name.lower() and firm == "2JS" and prize == "Kelly" and not designer:
                designer = "Kelly"
                prize = ""
                id_num = lookup_designer_phone(firm, designer)

            entries.append({
                "event_code": event_code,
                "event": EVENT_LABELS.get(event_code, event_code),
                "firm": firm,
                "id_number": id_num,
                "homeowner": ho_name,
                "homeowner_number": ho_num,
                "item_won": prize,
                "_source": f"rc:{'b23:' if is_backup else ''}{event_code}:{row['id']}[{item_idx}]",
                "_designer_internal": designer,
            })

    seen_form_keys: set[tuple] = set()
    for idx, form_row in enumerate(form_raw, 1):
        if idx in matched_form_indices or idx in spouse_matches:
            continue
        ho_phones = extract_sg_phones(form_row["Homeowner Number"])
        firm = canon_firm(form_row["Interior Design Company"])
        event_code = map_form_event(form_row["Event Name/Date"], form_row["Timestamp"])
        ho_num = format_phone_full(form_row["Homeowner Number"], ho_phones)
        prize = clean_str(form_row["Name of Prize"])
        dedup_key = (tuple(ho_phones) if ho_phones else (ho_num,), firm, event_code)
        if dedup_key in seen_form_keys:
            continue
        seen_form_keys.add(dedup_key)
        designer = clean_str(form_row["Designer Name"])
        id_num = format_phone_full(form_row["Designer Number"])
        if not id_num:
            id_num = lookup_designer_phone(firm, designer)
        entries.append({
            "event_code": event_code,
            "event": EVENT_LABELS.get(event_code, event_code),
            "firm": firm,
            "id_number": id_num,
            "homeowner": clean_str(form_row["Homeowner Name"]),
            "homeowner_number": ho_num,
            "item_won": prize,
            "_source": f"form:#{idx}",
            "_designer_internal": designer,
        })

    order_map = {code: index for index, code in enumerate(EVENT_ORDER)}
    entries.sort(key=lambda entry: (order_map.get(entry["event_code"], 999), entry["firm"].lower(), entry["homeowner"].lower()))
    return [
        {
            "event": entry["event"],
            "id_firm": entry["firm"],
            "id_number": entry["id_number"],
            "homeowner": entry["homeowner"],
            "homeowner_number": entry["homeowner_number"],
            "item_won": entry["item_won"],
        }
        for entry in entries
    ]


def _decrypt_text(path: Path, key_b64: str) -> str:
    if not path.exists():
        raise CryptoError(f"missing encrypted file {path.name}")
    if not key_b64:
        raise CryptoError(f"{path.name} is present but DATA_KEY is not set")
    return decrypt_file(path, key_b64).decode("utf-8")


def load_constants(path: Path, key_b64: str) -> dict:
    if not path.exists():
        logger.info("designer constants file missing; using empty maps")
        return normalize_constants({})
    if not key_b64:
        raise CryptoError(f"{path.name} is present but DATA_KEY is not set")
    payload = json.loads(decrypt_file(path, key_b64).decode("utf-8"))
    if not isinstance(payload, dict):
        raise CryptoError("designer constants must be a JSON object")
    return normalize_constants(payload)


def load_all() -> list[dict[str, str]]:
    data_dir = Path(os.environ.get("LUCKY_DRAW_DATA_DIR", DATA_DIR))
    key = os.environ.get("DATA_KEY", "").strip()
    pre = _decrypt_text(data_dir / "pre_renocode.csv.enc.b64.txt", key)
    form = _decrypt_text(data_dir / "form_responses.csv.enc.b64.txt", key)
    constants = load_constants(data_dir / "designer_constants.json.enc.b64.txt", key)
    rows = build_rows(pre, form, sb.get, constants)
    logger.info("built winner rows count=%s", len(rows))
    return rows
