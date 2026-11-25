"""In-memory winner cache. A failed pull keeps the previous rows."""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.refresh import load_all

logger = logging.getLogger(__name__)

SGT = ZoneInfo("Asia/Singapore")
DEFAULT_INTERVAL = 600
CHECK_SECONDS = 300
REFRESH_HOUR = 20
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_RANGE = re.compile(r"\((\d{1,2})(?:\s+([A-Za-z]{3}))?\s*[-\u2013]\s*(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})\)")
_SINGLE = re.compile(r"\((\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})\)")


def event_days_from_names(names) -> set[date]:
    """Event days plus the day after each event, parsed from names like 'RF33 (27-28 Nov 2026)'."""
    days: set[date] = set()
    for name in names:
        name = name or ""
        try:
            m = _RANGE.search(name)
            if m:
                d1, m1, d2, m2, yr = m.groups()
                end = date(int(yr), _MONTHS[m2.lower()], int(d2))
                start_month = _MONTHS[(m1 or m2).lower()]
                start_year = int(yr) - (1 if start_month > end.month else 0)
                start = date(start_year, start_month, int(d1))
            else:
                m = _SINGLE.search(name)
                if not m:
                    continue
                start = end = date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        except (KeyError, ValueError):
            continue
        if (end - start).days > 14:
            continue
        day = start
        while day <= end + timedelta(days=1):
            days.add(day)
            day += timedelta(days=1)
    return days


def latest_due_slot(days: set[date], now: datetime) -> datetime | None:
    """Most recent 8pm SGT slot on an event day (or day after) that is not in the future."""
    local = now.astimezone(SGT)
    for back in range(0, 60):
        day = local.date() - timedelta(days=back)
        if day in days:
            slot = datetime.combine(day, time(REFRESH_HOUR), SGT)
            if slot <= local:
                return slot
    return None


def _load_event_days() -> set[date]:
    from app import sb
    rows = sb.get("events?select=name")
    return event_days_from_names(row.get("name") for row in rows)


def format_sgt(moment: datetime | None) -> str:
    if moment is None:
        return "not yet"
    local = moment.astimezone(SGT)
    hour = local.strftime("%I").lstrip("0") or "12"
    clock = local.strftime(f"%d %b %Y, {hour}:%M %p").replace("AM", "am").replace("PM", "pm")
    return f"{clock} SGT"


class WinnerStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: list[dict[str, str]] = []
        self.updated_at: datetime | None = None
        self.last_error: str | None = None
        self.loaded = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.event_days: set[date] = set()

    def snapshot(self) -> tuple[list[dict[str, str]], datetime | None, str | None, bool]:
        with self._lock:
            return list(self.rows), self.updated_at, self.last_error, self.loaded

    def set_rows(self, rows: list[dict[str, str]], updated_at: datetime | None = None) -> None:
        with self._lock:
            self.rows = [dict(row) for row in rows]
            self.updated_at = updated_at or datetime.now(SGT)
            self.last_error = None
            self.loaded = True

    def refresh(self) -> bool:
        try:
            rows = load_all()
        except Exception as exc:
            logger.exception("refresh failed")
            with self._lock:
                self.last_error = exc.__class__.__name__
            return False
        try:
            days = _load_event_days()
        except Exception:
            logger.warning("could not load event days; keeping previous schedule")
            days = None
        with self._lock:
            if days is not None:
                self.event_days = days
            self.rows = rows
            self.updated_at = datetime.now(SGT)
            self.last_error = None
            self.loaded = True
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="winner-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _interval(self) -> float:
        raw = os.environ.get("REFRESH_INTERVAL_SECONDS", str(DEFAULT_INTERVAL))
        try:
            return max(1.0, float(raw))
        except ValueError:
            return float(DEFAULT_INTERVAL)

    def due(self, now: datetime | None = None) -> bool:
        with self._lock:
            days, updated, loaded = set(self.event_days), self.updated_at, self.loaded
        if not loaded or updated is None:
            return True
        slot = latest_due_slot(days, now or datetime.now(SGT))
        return slot is not None and updated < slot

    def _loop(self) -> None:
        # Load once at startup (the free instance keeps data only in memory),
        # then pull again only at 8pm SGT on event days and the day after each event.
        self.refresh()
        if os.environ.get("REFRESH_INTERVAL_SECONDS"):
            while not self._stop.wait(self._interval()):
                self.refresh()
            return
        while not self._stop.wait(CHECK_SECONDS):
            if self.due():
                self.refresh()


store = WinnerStore()
