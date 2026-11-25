"""Read-only Supabase REST helper.

Same request shape as the original workspace script: GET {SUPABASE_URL}/rest/v1/{path}
with the service-role key in headers, and an optional Range header. This module
never issues another HTTP method. Environment variables are read on each call
so importing the module does not require them.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class SupabaseError(Exception):
    def __init__(self, message: str, status: int = 0):
        self.status = status
        super().__init__(message)


def get(path: str, rng: str | None = None):
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        raise SupabaseError("supabase is not configured")
    headers = {"apikey": key, "Authorization": "Bearer " + key}
    if rng:
        headers["Range"] = rng
    request = urllib.request.Request(base + "/rest/v1/" + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SupabaseError(f"supabase read failed ({exc.code})", exc.code) from exc
    except urllib.error.URLError as exc:
        raise SupabaseError("supabase connection failed") from exc
    if not isinstance(payload, list):
        raise SupabaseError("supabase response was not a list")
    return payload
