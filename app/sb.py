"""Read-only Supabase REST helper.

Uses SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY. This module only issues GET
requests. It has no insert, update, delete, or RPC helpers.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000
MAX_PAGES = 200


class SupabaseError(Exception):
    def __init__(self, table: str, status: int):
        self.table = table
        self.status = status
        super().__init__(f"supabase read failed for {table} (HTTP {status})")


class SupabaseRest:
    """GET rows from /rest/v1/{table}. The key is sent as a header, never in the URL."""

    def __init__(self, url: str, service_role_key: str, client: httpx.Client):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.client = client
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Accept": "application/json",
        }

    def get_all(self, table: str, page_size: int = PAGE_SIZE) -> list[dict[str, Any]]:
        if not table or "/" in table or table.startswith("."):
            raise SupabaseError(table or "?", 400)
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            response = self._get_page(table, offset, page_size)
            if response.status_code == 416 and offset > 0:
                break
            if response.status_code >= 400:
                logger.error("supabase read failed table=%s status=%s", table, response.status_code)
                raise SupabaseError(table, response.status_code)
            batch = response.json()
            if not isinstance(batch, list):
                logger.error("supabase read failed table=%s status=200 unexpected body", table)
                raise SupabaseError(table, 200)
            rows.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < page_size:
                break
            offset += page_size
        else:
            logger.error("supabase read stopped at page cap table=%s", table)
            raise SupabaseError(table, 0)
        return rows

    def _get_page(self, table: str, offset: int, page_size: int) -> httpx.Response:
        url = self.base + quote(table, safe="")
        params = {"select": "*", "limit": str(page_size), "offset": str(offset)}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.client.get(url, headers=self.headers, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("supabase connection error table=%s attempt=%s", table, attempt + 1)
                continue
            if response.status_code in {502, 503, 504} and attempt == 0:
                logger.warning("supabase retry table=%s status=%s", table, response.status_code)
                continue
            return response
        logger.error("supabase connection failed table=%s", table)
        raise SupabaseError(table, 0) from last_error
