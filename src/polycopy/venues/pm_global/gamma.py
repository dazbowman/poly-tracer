"""Gamma API client (market/event catalog). Read-only, keyless."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from polycopy.util.http import PoliteClient

log = logging.getLogger(__name__)

BASE = "https://gamma-api.polymarket.com"


class GammaClient:
    def __init__(self, cache_dir: Path | None = None, rps: float = 4.0) -> None:
        self.http = PoliteClient(BASE, rps=rps, cache_dir=cache_dir)

    def markets_keyset(
        self,
        *,
        closed: bool,
        end_date_min: str | None = None,
        include_tag: bool = True,
        limit: int = 100,
        max_pages: int | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Crawl markets via /markets/keyset, yielding pages.

        `closed` is mandatory because the endpoint silently defaults to
        closed=false — a full catalog needs one crawl per value.
        Keyset pagination: pass back next_cursor as after_cursor until empty.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "order": "id",
            "ascending": "true",
            "include_tag": str(include_tag).lower(),
            "closed": str(closed).lower(),
        }
        if end_date_min:
            params["end_date_min"] = end_date_min
        cursor: str | None = None
        pages = 0
        while True:
            q = dict(params)
            if cursor:
                q["after_cursor"] = cursor
            body = self.http.get("/markets/keyset", q)
            markets = body.get("markets", [])
            if markets:
                yield markets
            cursor = body.get("next_cursor")
            pages += 1
            if not cursor or not markets or (max_pages and pages >= max_pages):
                return

    def markets_keyset_resumable(
        self,
        *,
        closed: bool,
        end_date_min: str | None = None,
        after_cursor: str | None = None,
        include_tag: bool = True,
        limit: int = 100,
        max_pages: int | None = None,
    ) -> Iterator[tuple[list[dict[str, Any]], str | None]]:
        """Like markets_keyset but yields (page, next_cursor) and can start from
        a saved cursor, so a killed crawl resumes from its last checkpoint."""
        params: dict[str, Any] = {
            "limit": limit,
            "order": "id",
            "ascending": "true",
            "include_tag": str(include_tag).lower(),
            "closed": str(closed).lower(),
        }
        if end_date_min:
            params["end_date_min"] = end_date_min
        cursor = after_cursor
        pages = 0
        while True:
            q = dict(params)
            if cursor:
                q["after_cursor"] = cursor
            body = self.http.get("/markets/keyset", q)
            markets = body.get("markets", [])
            cursor = body.get("next_cursor")
            if markets:
                yield markets, cursor
            pages += 1
            if not cursor or not markets or (max_pages and pages >= max_pages):
                return

    def market_by_condition(self, condition_ids: list[str]) -> list[dict[str, Any]]:
        return self.http.get(
            "/markets", {"condition_ids": condition_ids, "limit": len(condition_ids)}
        )
