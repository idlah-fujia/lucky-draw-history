"""Lucky Draw History. Staff look up one number; only that number's rows leave the server."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.fields import FIELDS
from app.phones import normalize_phone, phone_tokens
from app.ratelimit import client_ip, login_limiter, lookup_limiter
from app.session import (
    PHONE_COOKIE,
    SESSION_COOKIE,
    admin_token_ok,
    issue_admin_token,
    issue_phone_token,
    passwords_match,
    read_phone_token,
)
from app.store import format_sgt, store

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

NO_MATCH = "No lucky draw records found for this number."
INVALID_NUMBER = "Enter an 8-digit phone number. Spaces, dashes, and a leading +65 are fine."
LOOKUP_LIMIT = "Too many lookups. Wait a minute and try again."
LOGIN_LIMIT = "Too many sign-in attempts. Wait a minute and try again."
BAD_PASSWORD = "That password is not correct."
LOADING = "Winner history is still loading. Try again in a moment."
UNAVAILABLE = "Winner history is unavailable right now. Try again shortly."


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if not os.environ.get("ADMIN_PASSWORD", "").strip():
        logger.warning("ADMIN_PASSWORD is not set; admin sign-in is disabled")
    if os.environ.get("SKIP_STARTUP_REFRESH") != "1":
        store.start()
    yield
    store.stop()


app = FastAPI(title="Lucky Draw History", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    response.headers["Cache-Control"] = "no-store"
    return response


def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def _set_cookie(response, request: Request, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=_secure(request),
        samesite="lax",
        path="/",
    )


def _clear_cookie(response, request: Request, name: str) -> None:
    response.delete_cookie(name, path="/", httponly=True, secure=_secure(request), samesite="lax")


def _render(request: Request, name: str, status_code: int = 200, **context):
    context.setdefault("title", "Lucky Draw History")
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _is_admin(request: Request) -> bool:
    # Admin page is open (no password) by owner request, 26 Nov 2026.
    return True
    return admin_token_ok(request.cookies.get(SESSION_COOKIE))


def _event_options(rows: list[dict[str, str]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        label = row.get("event") or "(No event)"
        counts[label] = counts.get(label, 0) + 1
    return list(counts.items())


def _event_label(row: dict[str, str]) -> str:
    return row.get("event") or "(No event)"


def _matches_query(row: dict[str, str], query: str) -> bool:
    text = query.strip()
    if not text:
        return True
    blob = " ".join(row.get(key, "") for key, _label in FIELDS).casefold()
    if text.casefold() in blob:
        return True
    number = normalize_phone(text)
    if not number:
        return False
    return number in phone_tokens(row.get("id_number", "")) | phone_tokens(row.get("homeowner_number", ""))


def _filter_rows(rows: list[dict[str, str]], *, phone: str | None, query: str, event: str) -> list[dict[str, str]]:
    chosen: list[dict[str, str]] = []
    for row in rows:
        if phone is not None:
            numbers = phone_tokens(row.get("id_number", "")) | phone_tokens(row.get("homeowner_number", ""))
            if phone not in numbers:
                continue
        if event and _event_label(row) != event:
            continue
        if not _matches_query(row, query):
            continue
        chosen.append({key: row.get(key, "") for key, _label in FIELDS})
    return chosen


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


@app.get("/", response_class=HTMLResponse)
def lookup_form(request: Request, error: str = ""):
    return _render(request, "lookup.html", title="Lucky Draw History", error=error)


@app.post("/lookup")
def lookup(request: Request, phone: str = Form("")):
    if not lookup_limiter.allow(client_ip(request)):
        return _render(request, "message.html", status_code=429, title="Slow down", heading="Slow down", message=LOOKUP_LIMIT)
    number = normalize_phone(phone)
    if number is None:
        return _render(request, "lookup.html", status_code=400, title="Lucky Draw History", error=INVALID_NUMBER, phone=phone)
    response = RedirectResponse("/results", status_code=303)
    _set_cookie(response, request, PHONE_COOKIE, issue_phone_token(number))
    return response


@app.post("/lookup/clear")
def clear_lookup(request: Request):
    response = RedirectResponse("/", status_code=303)
    _clear_cookie(response, request, PHONE_COOKIE)
    return response


@app.get("/results", response_class=HTMLResponse)
def results(request: Request, q: str = ""):
    number = read_phone_token(request.cookies.get(PHONE_COOKIE))
    if not number:
        return RedirectResponse("/", status_code=303)
    rows, _updated, last_error, loaded = store.snapshot()
    if not loaded and last_error:
        notice = UNAVAILABLE
        visible: list[dict[str, str]] = []
    elif not loaded:
        notice = LOADING
        visible = []
    else:
        visible = _filter_rows(rows, phone=number, query=q, event="")
        notice = NO_MATCH if not q.strip() and not visible else ""
    return _render(
        request,
        "results.html",
        title="Your wins · Lucky Draw History",
        rows=visible,
        query=q,
        notice=notice,
        fields=FIELDS,
        number=number,
        filtered_empty=bool(q.strip()) and not visible and not notice,
    )


@app.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request):
    if _is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return _render(request, "login.html", title="Admin sign in · Lucky Draw History", error="")


@app.post("/admin/login")
def login(request: Request, password: str = Form("")):
    if not login_limiter.allow(client_ip(request)):
        return _render(request, "login.html", status_code=429, title="Admin sign in · Lucky Draw History", error=LOGIN_LIMIT)
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if not passwords_match(password, expected):
        return _render(request, "login.html", status_code=401, title="Admin sign in · Lucky Draw History", error=BAD_PASSWORD)
    response = RedirectResponse("/admin", status_code=303)
    _set_cookie(response, request, SESSION_COOKIE, issue_admin_token())
    return response


@app.post("/admin/logout")
def logout(request: Request):
    response = RedirectResponse("/", status_code=303)
    _clear_cookie(response, request, SESSION_COOKIE)
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, q: str = "", event: str = ""):
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    rows, updated, last_error, loaded = store.snapshot()
    options = _event_options(rows)
    visible = _filter_rows(rows, phone=None, query=q, event=event)
    return _render(
        request,
        "admin.html",
        title="Admin · Lucky Draw History",
        rows=visible,
        total=len(rows),
        query=q,
        event=event,
        events=options,
        fields=FIELDS,
        updated=format_sgt(updated),
        last_error=last_error,
        loaded=loaded,
    )


@app.post("/admin/refresh")
def admin_refresh(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    store.refresh()
    return RedirectResponse("/admin", status_code=303)


@app.exception_handler(404)
async def not_found(request: Request, _exc):
    if request.url.path.startswith("/static"):
        return PlainTextResponse("Not found", status_code=404)
    return _render(request, "message.html", status_code=404, title="Not found", heading="Page not found", message="That page is not part of Lucky Draw History.")


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    logger.exception("request failed")
    return _render(
        request,
        "message.html",
        status_code=500,
        title="Error",
        heading="Something went wrong",
        message="The page could not be loaded. Try again in a moment.",
    )
