"""Polymarket Data API v2 client (read-only, keyless).

Envelope: {data, pagination{limit, offset, has_more, next_cursor}} — signed
cursor, so pages are walked strictly via next_cursor (DATA_NOTES).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl

from polycopy.util.http import PoliteClient

log = logging.getLogger(__name__)

BASE = "https://data-api.polymarket.com"


class DataApiClient:
    def __init__(self, cache_dir: Path | None = None, rps: float = 4.0) -> None:
        self.http = PoliteClient(BASE, rps=rps, cache_dir=cache_dir)

    def _paged(
        self, path: str, params: dict[str, Any], *, max_pages: int | None = None
    ) -> Iterator[list[dict]]:
        cursor = None
        pages = 0
        while True:
            q = dict(params)
            if cursor:
                q["cursor"] = cursor
            body = self.http.get(path, q)
            data = body.get("data", [])
            if data:
                yield data
            pg = body.get("pagination", {})
            cursor = pg.get("next_cursor")
            pages += 1
            if not pg.get("has_more") or not cursor or (max_pages and pages >= max_pages):
                return

    def trades(
        self,
        user: str,
        *,
        start: int | None = None,
        end: int | None = None,
        limit: int = 500,
        max_pages: int | None = None,
    ) -> Iterator[list[dict]]:
        params: dict[str, Any] = {"user": user, "limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        yield from self._paged("/v2/trades", params, max_pages=max_pages)

    def leaderboard(
        self, *, time_period: str = "month", sort_by: str = "PNL", max_pages: int = 10
    ) -> list[dict]:
        rows: list[dict] = []
        for page in self._paged(
            "/v2/leaderboard",
            {"time_period": time_period, "sort_by": sort_by, "limit": 100},
            max_pages=max_pages,
        ):
            rows.extend(page)
        return rows

    def prices_history(
        self, token_id: str, *, start: int, end: int, bucket_seconds: int | None = 300
    ) -> list[dict]:
        """bucket_seconds=None omits the param: the API then serves its native
        stored resolution (older markets keep only coarse buckets and return an
        EMPTY series — not an error — when a finer bucket_seconds is pinned)."""
        params: dict[str, Any] = {"token_id": token_id, "start": start, "end": end}
        if bucket_seconds is not None:
            params["bucket_seconds"] = bucket_seconds
        out: list[dict] = []
        for page in self._paged("/v2/prices-history", params):
            out.extend(page)
        return out


def normalize_api_trades(raw: list[dict], token_map: pl.DataFrame) -> pl.DataFrame:
    """API trade rows -> fills_norm schema (source='api').

    API rows are the wallet's own trade events (one leg), with side BUY|SELL;
    role is unknown from this endpoint and stored as 'api'.
    """
    if not raw:
        return pl.DataFrame()
    df = pl.DataFrame(
        [
            {
                "ts": r["timestamp"],
                "wallet": r["proxy_wallet"].lower(),
                "condition_id": r["condition_id"].lower(),
                "token_id": str(r["token_id"]),
                "shares": float(r["size"]),
                "price": float(r["price"]),
                "bought": r["side"] == "BUY",
                "outcome_index": r.get("outcome_index"),
            }
            for r in raw
        ]
    )
    tm = token_map.select("token_id", "is_yes")
    return (
        df.join(tm, on="token_id", how="left")
        .with_columns(
            is_yes=pl.coalesce(pl.col("is_yes"), pl.col("outcome_index") == 0),
        )
        .with_columns(
            canon_dir=pl.when(pl.col("bought") == pl.col("is_yes")).then(1).otherwise(-1),
            usdc=pl.col("shares") * pl.col("price"),
            price_yes_equiv=pl.when(pl.col("is_yes"))
            .then(pl.col("price"))
            .otherwise(1.0 - pl.col("price")),
            role=pl.lit("api"),
        )
        .select(
            "ts",
            "wallet",
            "condition_id",
            "token_id",
            "canon_dir",
            "shares",
            "usdc",
            "price_yes_equiv",
            "bought",
            "role",
        )
    )
