"""In-memory winner cache. A failed pull keeps the previous rows."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.refresh import load_all

logger = logging.getLogger(__name__)

SGT = ZoneInfo("Asia/Singapore")
DEFAULT_INTERVAL = 600


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
        with self._lock:
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

    def _loop(self) -> None:
        self.refresh()
        while not self._stop.wait(self._interval()):
            self.refresh()


store = WinnerStore()
