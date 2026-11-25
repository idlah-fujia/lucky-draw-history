import json

from app.refresh import (
    build_rows,
    canon_firm,
    fetch_all_sb,
    load_constants,
    map_form_event,
    normalize_constants,
)
from app.sb import get as sb_get

PRE_HEADER = "entry_id,event_code,firm,firm_raw,designer,designer_phone,designer_phone_raw,homeowner_name,homeowner_phone,homeowner_phone_raw,prize"
FORM_HEADER = "Timestamp,Event Name/Date,Homeowner Name,Homeowner Number,Interior Design Company,Designer Name,Designer Number,Name of Prize"

CONSTANTS = {
    "VERIFIED_TYPO_CANONICAL": {"2JS|kelly": "31112222"},
    "WHATSAPP_KNOWN_NUMBERS": {"Elysian Design|ned": "34445556"},
    "FORM_SPOUSE_MATCH_TO_EXISTING": {},
}


def _tables():
    attendees = [
        {
            "id": "a22",
            "event_id": "e22",
            "name": "Jo Tan",
            "phone": "63434567",
            "lucky_draw": [{"firm": "Supreme Houzz", "designer": "", "prize": "Lamp"}],
            "lucky_draw_enrolled": True,
        },
        {
            "id": "a25",
            "event_id": "e25",
            "name": "Tiffany Lim",
            "phone": "30120121",
            "lucky_draw": [{"firm": "2J studio", "designer": "", "prize": "Kelly"}],
        },
        {
            "id": "a20",
            "event_id": "e20",
            "name": "Aaishah Test",
            "phone": "30210212",
            "lucky_draw": [{"firm": "Onyx", "designer": "Dee", "prize": "lkio"}],
        },
        {
            "id": "aclara",
            "event_id": "e25",
            "name": "Clara (RF testing)",
            "phone": "30220222",
            "lucky_draw": [{"firm": "Onyx", "designer": "Dee", "prize": ""}],
        },
        {
            "id": "alynn",
            "event_id": "e25",
            "name": "Lynn Chiam",
            "phone": "30230232",
            "lucky_draw": [{"firm": "Onyx", "designer": "", "prize": ""}],
        },
        {
            "id": "aempty",
            "event_id": "e25",
            "name": "Empty Slot",
            "phone": "30240242",
            "lucky_draw": [{"firm": "", "designer": "", "prize": ""}],
        },
        {
            "id": "arin",
            "event_id": "e25",
            "name": "Rin Ho",
            "phone": "30150151",
            "lucky_draw": [{"firm": "", "designer": "Dee", "prize": ""}],
        },
        {
            "id": "asam",
            "event_id": "e10",
            "name": "Sam Lee",
            "phone": "62342342",
            "lucky_draw": [{"firm": "ciseern", "designer": "Avery", "prize": "Voucher"}],
        },
        {
            "id": "aned",
            "event_id": "e25",
            "name": "Ned Ho",
            "phone": "30250252",
            "lucky_draw": [{"firm": "Elysian", "designer": "Ned", "prize": "Book"}],
        },
    ]
    backup = [
        {
            "id": "b1",
            "event_id": "ignored",
            "name": "Backup Person",
            "phone": "30160161",
            "lucky_draw": [{"firm": "inori", "designer": "Dee", "prize": "Mug"}],
        }
    ]
    logs = [
        {
            "event_id": "e25",
            "attendee_id": "arin",
            "created_at": "2026-01-01T00:00:00Z",
            "detail": {"lucky_draw": [{"firm": "Fyner", "designer": "I", "prize": "Voucher"}]},
        }
    ]
    events = [
        {"id": "e22", "code": "RF22"},
        {"id": "e25", "code": "RF25"},
        {"id": "e20", "code": "RF20"},
        {"id": "e10", "code": "RF10"},
    ]

    def fake(path, rng=None):
        if path.startswith("events"):
            return events
        if path.startswith("attendees_rf23"):
            return backup if rng == "0-999" else []
        if path.startswith("attendees?"):
            return attendees if rng == "0-999" else []
        if path.startswith("audit_log"):
            return logs if rng == "0-999" else []
        raise AssertionError(path)

    return fake


def _sources():
    pre = "\n".join([
        PRE_HEADER,
        "p1,RF9,ciseern,,Avery Quek,31234567,,Alex Tan,61234567,,Hamper",
        "p2,RF22,supreme,,Lee,32324567,,Jo Tan,63434567,,Lamp",
    ])
    form = "\n".join([
        FORM_HEADER,
        "01/01/2026,RF9,Alex Tan,61234567,Ciseern,Avery Quek,31234567,Hamper",
        "02/01/2026,RF32,Kim Ong,64564567,Sora,Pat,35674567,Fan",
        "02/01/2026,RF32,Kim Ong,64564567,Sora,Pat,35674567,Fan",
        "18/10/2025 09:00,idlah,Old One,36786781,Brick,Sam,37896781,Mug",
    ])
    return pre, form


def test_firm_and_form_event_rules():
    assert canon_firm("CDG Central team") == "CDG Central"
    assert canon_firm("conquest west") == "Conquest Design Group"
    assert canon_firm("  VII  ") == "VII LUXE"
    assert map_form_event("RF 9 weekend", "") == "RF9"
    assert map_form_event("Nova Furnishing hall", "") == "RF32"
    assert map_form_event("idlah", "18/10/2025 09:00") == "PRE_UNKNOWN"
    assert map_form_event("something else", "01/01/2026") == "PRE_UNKNOWN"


def test_build_rows_matches_original_rules():
    pre, form = _sources()
    rows = build_rows(pre, form, _tables(), CONSTANTS)
    assert rows == [
        {
            "event": "RF32 (14–15 Nov 2026)",
            "id_firm": "Sora Studios",
            "id_number": "35674567",
            "homeowner": "Kim Ong",
            "homeowner_number": "64564567",
            "item_won": "Fan",
        },
        {
            "event": "RF25 (14–16 Aug 2026)",
            "id_firm": "2JS",
            "id_number": "31112222",
            "homeowner": "Tiffany Lim",
            "homeowner_number": "30120121",
            "item_won": "",
        },
        {
            "event": "RF25 (14–16 Aug 2026)",
            "id_firm": "Elysian Design",
            "id_number": "34445556",
            "homeowner": "Ned Ho",
            "homeowner_number": "30250252",
            "item_won": "Book",
        },
        {
            "event": "RF25 (14–16 Aug 2026)",
            "id_firm": "Fyner Studio",
            "id_number": "",
            "homeowner": "Rin Ho",
            "homeowner_number": "30150151",
            "item_won": "Voucher",
        },
        {
            "event": "RF23 (11–12 Jul 2026)",
            "id_firm": "Inori",
            "id_number": "",
            "homeowner": "Backup Person",
            "homeowner_number": "30160161",
            "item_won": "Mug",
        },
        {
            "event": "RF22 (27–28 Jun 2026)",
            "id_firm": "Supreme Houzz",
            "id_number": "32324567",
            "homeowner": "Jo Tan",
            "homeowner_number": "63434567",
            "item_won": "Lamp",
        },
        {
            "event": "RF10 (1–2 Nov 2025)",
            "id_firm": "Ciseern",
            "id_number": "31234567",
            "homeowner": "Sam Lee",
            "homeowner_number": "62342342",
            "item_won": "Voucher",
        },
        {
            "event": "RF9 (25–26 Oct 2025)",
            "id_firm": "Ciseern",
            "id_number": "31234567",
            "homeowner": "Alex Tan",
            "homeowner_number": "61234567",
            "item_won": "Hamper",
        },
        {
            "event": "Unspecified Event",
            "id_firm": "Brick & Deco",
            "id_number": "37896781",
            "homeowner": "Old One",
            "homeowner_number": "36786781",
            "item_won": "Mug",
        },
    ]
    assert all(set(row) == {"event", "id_firm", "id_number", "homeowner", "homeowner_number", "item_won"} for row in rows)
    assert not any(row["homeowner"] in {"Aaishah Test", "Clara (RF testing)", "Lynn Chiam", "Empty Slot"} for row in rows)
    assert sum(1 for row in rows if row["homeowner"] == "Jo Tan") == 1
    assert sum(1 for row in rows if row["homeowner"] == "Kim Ong") == 1


def test_spouse_match_uses_the_pre_row_once():
    pre = "\n".join([
        PRE_HEADER,
        "p9,RF9,empyrean,,,,,Firdaus,61230000,,Card",
    ])
    form = "\n".join([
        FORM_HEADER,
        "01/02/2026,RF9,Hanis,61230000,Empyrean,Ann,33330000,Card",
    ])
    constants = {
        "FORM_SPOUSE_MATCH_TO_EXISTING": {"1": ["PRE", "RF9", "Empyrean", "61230000"]},
    }

    def fake(path, rng=None):
        if path.startswith("events"):
            return []
        return [] if rng else []

    rows = build_rows(pre, form, fake, constants)
    assert rows == [
        {
            "event": "RF9 (25–26 Oct 2025)",
            "id_firm": "Empyrean",
            "id_number": "33330000",
            "homeowner": "Firdaus",
            "homeowner_number": "61230000",
            "item_won": "Card",
        }
    ]


def test_constants_default_empty_when_file_missing(tmp_path):
    assert load_constants(tmp_path / "missing.json.enc.b64.txt", "") == normalize_constants({})


def test_fetch_all_pages_until_a_short_batch():
    calls = []

    def fake(path, rng=None):
        calls.append(rng)
        if rng == "0-999":
            return [{"id": index} for index in range(1000)]
        if rng == "1000-1999":
            return [{"id": 1000}]
        return []

    rows = fetch_all_sb(fake, "attendees?select=*")
    assert len(rows) == 1001
    assert calls == ["0-999", "1000-1999"]


def test_supabase_get_is_read_only(monkeypatch):
    seen = {}

    class Response:
        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout=0):
        seen["method"] = request.get_method()
        seen["url"] = request.full_url
        seen["range"] = request.headers["Range"]
        seen["leaked"] = "service-role-key" in request.full_url
        return Response()

    monkeypatch.setattr("app.sb.urllib.request.urlopen", urlopen)
    monkeypatch.setenv("SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    assert sb_get("events?select=*", rng="0-999") == []
    assert seen == {
        "method": "GET",
        "url": "https://example.test/rest/v1/events?select=*",
        "range": "0-999",
        "leaked": False,
    }


def test_note_keys_are_ignored():
    raw = {"_note": "ignore", "VERIFIED_TYPO_CANONICAL": {}, "WHATSAPP_KNOWN_NUMBERS": {}, "FORM_SPOUSE_MATCH_TO_EXISTING": {}}
    assert normalize_constants(json.loads(json.dumps(raw)))["VERIFIED_TYPO_CANONICAL"] == {}
