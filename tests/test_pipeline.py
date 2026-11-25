import json

import httpx

from app.phones import normalize_phone, phone_tokens
from app.refresh import build_rows, load_constants
from app.sb import SupabaseRest


def test_normalise_lookup_numbers():
    assert normalize_phone("1111 2222") == "11112222"
    assert normalize_phone("1111-2222") == "11112222"
    assert normalize_phone("+65 1111 2222") == "11112222"
    assert normalize_phone("6511112222") == "11112222"
    assert normalize_phone("1111222") is None
    assert normalize_phone("111122223") is None
    assert phone_tokens("11112222 / 33334444") == {"11112222", "33334444"}


def test_build_rows_merges_dedupes_and_enriches():
    pre = "Event,ID Firm,ID Number,Homeowner,Homeowner Number,Item Won\nRF0,Old Firm,22223333,Alex Tan,44445555,Hamper\n"
    form = "Which event?,Designer firm,Designer contact number,Homeowner name,Homeowner contact number,Prize\nRF0,Old Firm,22223333,Alex Tan,44445555,Hamper\nRF9,Beta,14141414,Kim Ong,15151515,Fan\n"
    tables = {
        "events": [{"id": "e1", "name": "RF1 (1 Jan 2026)"}],
        "attendees": [
            {
                "id": "a1",
                "event_id": "e1",
                "firm": "acme design",
                "designer_name": "Avery Quek",
                "homeowner_name": "Pat Lee",
                "homeowner_phone": "11112222",
                "prize": "",
                "is_winner": True,
            },
            {
                "id": "a2",
                "event_id": "e1",
                "firm": "Skip Co",
                "homeowner_name": "Not A Winner",
                "prize": "Nothing",
                "is_winner": False,
            },
        ],
        "attendees_rf23_draft_backup": [
            {
                "id": "b1",
                "event": "RF23 draft",
                "id_firm": "Draft Studio",
                "id_number": "16161616",
                "homeowner": "Rin Ho",
                "homeowner_number": "17171717",
                "item_won": "Mug",
            }
        ],
        "audit_log": [
            {"attendee_id": "a1", "field": "prize", "new_value": "Oak voucher", "created_at": "2026-01-02"},
        ],
    }
    constants = {
        "FIRM_ALIASES": {"acme design": "Acme Design"},
        "VERIFIED_TYPO_CANONICAL": {"16161616": "18181818"},
        "WHATSAPP_KNOWN_NUMBERS": {"Acme Design|Avery Quek": "19191919"},
    }
    rows = build_rows(pre_csv=pre, form_csv=form, tables=tables, constants=constants)
    assert rows[0]["homeowner"] == "Alex Tan"
    assert rows[1]["event"] == "RF1 (1 Jan 2026)"
    assert rows[1]["id_firm"] == "Acme Design"
    assert rows[1]["id_number"] == "19191919"
    assert rows[1]["item_won"] == "Oak voucher"
    assert rows[1]["homeowner"] == "Pat Lee"
    assert rows[2]["id_number"] == "18181818"
    assert rows[2]["homeowner"] == "Rin Ho"
    assert rows[3]["homeowner"] == "Kim Ong"
    assert all(set(row) == {"event", "id_firm", "id_number", "homeowner", "homeowner_number", "item_won"} for row in rows)
    assert not any(row["homeowner"] == "Not A Winner" for row in rows)
    assert len(rows) == 4


def test_constants_default_empty_when_file_missing(tmp_path):
    assert load_constants(tmp_path / "missing.json.enc.b64.txt", "") == {
        "VERIFIED_TYPO_CANONICAL": {},
        "WHATSAPP_KNOWN_NUMBERS": {},
        "FIRM_ALIASES": {},
    }


def test_supabase_client_is_get_only_and_pages(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        offset = int(request.url.params["offset"])
        if offset == 0:
            body = [{"id": "1"}, {"id": "2"}]
        else:
            body = [{"id": "3"}]
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = SupabaseRest("https://example.test", "service-role-key", client)
    rows = api.get_all("attendees", page_size=2)
    assert rows == [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    assert {call.method for call in calls} == {"GET"}
    assert all("service-role-key" not in str(call.url) for call in calls)
    assert calls[0].headers["apikey"] == "service-role-key"
    assert calls[0].headers["authorization"] == "Bearer service-role-key"


def test_optional_note_keys_are_ignored():
    raw = json.dumps({"_note": "ignore", "FIRM_ALIASES": {"old": "New"}})
    from app.refresh import normalize_constants

    assert normalize_constants(json.loads(raw))["FIRM_ALIASES"] == {"old": "New"}
