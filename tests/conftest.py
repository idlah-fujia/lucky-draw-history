import os
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["SESSION_SECRET"] = "test-session-secret-value"
os.environ["SKIP_STARTUP_REFRESH"] = "1"
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
os.environ.pop("DATA_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ratelimit import login_limiter, lookup_limiter
from app.store import store

ROWS = [
    {
        "event": "RF1 (1 Jan 2026)",
        "id_firm": "North Studio",
        "id_number": "11112222",
        "homeowner": "Pat Lee",
        "homeowner_number": "33334444",
        "item_won": "Oak voucher",
    },
    {
        "event": "RF2 (2 Feb 2026)",
        "id_firm": "South Studio",
        "id_number": "55556666",
        "homeowner": "Jo Tan",
        "homeowner_number": "77778888",
        "item_won": "Brass lamp",
    },
]


@pytest.fixture
def client():
    store.set_rows(ROWS, updated_at=datetime(2026, 11, 25, 16, 47, tzinfo=ZoneInfo("Asia/Singapore")))
    lookup_limiter.reset()
    login_limiter.reset()
    with TestClient(app) as test_client:
        yield test_client
