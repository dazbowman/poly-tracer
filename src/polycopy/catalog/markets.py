"""Build the global market catalog (SPEC §6 `markets`) from Gamma.

One row per condition. Canonical outcome = outcome index 0 (its token is
`yes_token_id` even when the label isn't literally "Yes"); `payout_yes` is the
resolved payout of that canonical outcome: 1, 0, 0.5, or null while unresolved.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import polars as pl

from polycopy.catalog.categories import categorize
from polycopy.util.timeutil import parse_ts_ms

log = logging.getLogger(__name__)

SCHEMA = {
    "condition_id": pl.Utf8,
    "market_slug": pl.Utf8,
    "gamma_id": pl.Int64,
    "event_id": pl.Utf8,
    "event_slug": pl.Utf8,
    "question": pl.Utf8,
    "yes_token_id": pl.Utf8,
    "no_token_id": pl.Utf8,
    "outcome_yes_label": pl.Utf8,
    "outcome_no_label": pl.Utf8,
    "neg_risk": pl.Boolean,
    "category_raw": pl.Utf8,
    "category_bucket": pl.Utf8,
    "start_ts": pl.Int64,
    "end_ts": pl.Int64,
    "resolved_ts": pl.Int64,
    "payout_yes": pl.Float64,
    "status": pl.Utf8,
    "game_start_ts": pl.Int64,
    "volume_usd": pl.Float64,
    "uma_status": pl.Utf8,
}


def _parse_json_list(s: Any) -> list:
    if isinstance(s, list):
        return s
    if isinstance(s, str) and s:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return []
    return []


def _payout_from_prices(prices: list, closed: bool, uma_status: str | None) -> float | None:
    """Resolved payout of outcome 0, from Gamma outcomePrices, only when resolved."""
    if not closed or len(prices) != 2:
        return None
    try:
        p0, p1 = float(prices[0]), float(prices[1])
    except (TypeError, ValueError):
        return None
    for want0, want1, payout in ((1, 0, 1.0), (0, 1, 0.0), (0.5, 0.5, 0.5)):
        if abs(p0 - want0) < 1e-9 and abs(p1 - want1) < 1e-9:
            return payout
    # closed but prices aren't hard 0/1/0.5 — resolution not final (or odd market)
    if uma_status == "resolved":
        log.debug("closed+resolved with soft prices: %s", prices)
    return None


def normalize_market(m: dict[str, Any]) -> dict[str, Any] | None:
    """Gamma market dict -> catalog row; None if not a 2-token CLOB market."""
    tokens = _parse_json_list(m.get("clobTokenIds"))
    if len(tokens) != 2 or not m.get("conditionId"):
        return None
    outcomes = _parse_json_list(m.get("outcomes"))
    if len(outcomes) != 2:
        outcomes = ["Yes", "No"]
    events = m.get("events") or []
    ev = events[0] if events else {}
    tags = [t.get("slug", "") for t in (m.get("tags") or []) if isinstance(t, dict)]
    start_ts = parse_ts_ms(m.get("startDate"))
    end_ts = parse_ts_ms(m.get("endDate"))
    closed = bool(m.get("closed"))
    uma_status = m.get("umaResolutionStatus")
    prices = _parse_json_list(m.get("outcomePrices"))
    payout = _payout_from_prices(prices, closed, uma_status)
    resolved_ts = parse_ts_ms(m.get("closedTime")) if closed else None
    if closed:
        status = "resolved" if payout is not None else "closed_unresolved"
    elif m.get("active"):
        status = "open"
    else:
        status = "inactive"
    question = m.get("question") or ""
    slug = m.get("slug") or ""
    return {
        "condition_id": m["conditionId"].lower(),
        "market_slug": slug,
        "gamma_id": int(m["id"]),
        "event_id": str(ev.get("id")) if ev.get("id") is not None else None,
        "event_slug": ev.get("slug"),
        "question": question,
        "yes_token_id": str(tokens[0]),
        "no_token_id": str(tokens[1]),
        "outcome_yes_label": str(outcomes[0]),
        "outcome_no_label": str(outcomes[1]),
        "neg_risk": bool(m.get("negRisk")),
        "category_raw": ",".join(tags),
        "category_bucket": categorize(tags, slug, question, end_ts, start_ts),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "resolved_ts": resolved_ts,
        "payout_yes": payout,
        "status": status,
        "game_start_ts": parse_ts_ms(m.get("gameStartTime")),
        "volume_usd": float(m.get("volumeNum") or 0.0),
        "uma_status": uma_status,
    }


def rows_to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def write_catalog(frames: list[pl.DataFrame], out: Path) -> pl.DataFrame:
    df = pl.concat(frames).unique(subset=["condition_id"], keep="last").sort("gamma_id")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out, compression="zstd")
    return df
