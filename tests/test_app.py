from tests.conftest import ROWS


OTHER = ("Jo Tan", "Brass lamp", "55556666", "South Studio")


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_admin_requires_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    page = client.get("/admin", follow_redirects=True)
    assert "Pat Lee" not in page.text
    assert "Oak voucher" not in page.text
    assert "Sign in" in page.text


def test_admin_rejects_bad_password(client):
    response = client.post("/admin/login", data={"password": "nope"}, follow_redirects=False)
    assert response.status_code == 401
    assert "That password is not correct." in response.text
    assert "Pat Lee" not in response.text


def test_admin_full_list_search_and_event_filter(client):
    response = client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Pat Lee" in response.text
    assert "Jo Tan" in response.text
    assert "Last updated 25 Nov 2026, 4:47 pm SGT" in response.text

    by_event = client.get("/admin?event=RF2 (2 Feb 2026)")
    assert "Jo Tan" in by_event.text
    assert "Pat Lee" not in by_event.text

    by_query = client.get("/admin?q=oak")
    assert "Oak voucher" in by_query.text
    assert "Brass lamp" not in by_query.text


def test_staff_lookup_returns_only_matching_rows(client):
    response = client.post("/lookup", data={"phone": "+65 3333 4444"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Oak voucher" in response.text
    assert "Pat Lee" in response.text
    for secret in OTHER:
        assert secret not in response.text


def test_number_normalisation(client):
    for typed in ("3333 4444", "3333-4444", "+65 3333 4444", "65 33334444"):
        lookup_reset = client
        response = lookup_reset.post("/lookup", data={"phone": typed}, follow_redirects=True)
        assert response.status_code == 200
        assert "Oak voucher" in response.text
        assert "Jo Tan" not in response.text


def test_no_match_message(client):
    response = client.post("/lookup", data={"phone": "12121212"}, follow_redirects=True)
    assert "No lucky draw records found for this number." in response.text
    assert "Pat Lee" not in response.text
    assert "Jo Tan" not in response.text


def test_invalid_number_stays_on_the_form(client):
    response = client.post("/lookup", data={"phone": "1234"}, follow_redirects=False)
    assert response.status_code == 400
    assert "8-digit" in response.text
    assert "No lucky draw records found for this number." not in response.text


def test_change_number_clears_the_lookup(client):
    client.post("/lookup", data={"phone": "33334444"}, follow_redirects=True)
    cleared = client.post("/lookup/clear", follow_redirects=False)
    assert cleared.status_code == 303
    again = client.get("/results", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/"


def test_search_on_results_stays_inside_the_matched_rows(client):
    client.post("/lookup", data={"phone": "11112222"}, follow_redirects=True)
    narrowed = client.get("/results?q=voucher")
    assert "Oak voucher" in narrowed.text
    assert "Brass lamp" not in narrowed.text
    missed = client.get("/results?q=brass")
    assert "No matching wins in this list." in missed.text
    assert "Brass lamp" not in missed.text


def test_rate_limit_uses_rightmost_forwarded_for(client):
    for index in range(10):
        response = client.post(
            "/lookup",
            data={"phone": "12121212"},
            headers={"X-Forwarded-For": f"1.2.3.{index}, 10.2.2.2"},
            follow_redirects=False,
        )
        assert response.status_code == 303
    blocked = client.post(
        "/lookup",
        data={"phone": "12121212"},
        headers={"X-Forwarded-For": "1.2.3.4, 10.2.2.2"},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert "Too many lookups" in blocked.text
    assert "Pat Lee" not in blocked.text
    other = client.post(
        "/lookup",
        data={"phone": "33334444"},
        headers={"X-Forwarded-For": "10.2.2.2, 10.4.4.4"},
        follow_redirects=True,
    )
    assert other.status_code == 200
    assert "Oak voucher" in other.text
    assert "Jo Tan" not in other.text


def test_refresh_keeps_last_good_rows(client, monkeypatch):
    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr("app.store.load_all", boom)
    client.post("/admin/login", data={"password": "test-admin-password"})
    response = client.post("/admin/refresh", follow_redirects=True)
    assert response.status_code == 200
    assert "RuntimeError" in response.text
    assert "Pat Lee" in response.text
    assert "Jo Tan" in response.text


def test_rows_constant_is_used():
    assert len(ROWS) == 2
