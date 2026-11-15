"""Probe every data source in SPEC §3: auth, schema, pagination, geoblock behavior.

Saves trimmed sample responses to tests/fixtures/ and prints a PASS/FAIL summary.
Re-runnable at any time; exits non-zero if any probe hard-fails.

Usage: uv run python scripts/probe_endpoints.py [--skip-hf]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
UA = "polycopy-research/0.1 (+https://github.com/dazbowman/poly-tracer; research, read-only)"

DATA_API = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
US_GW = "https://gateway.polymarket.us"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def trim(obj: Any, max_list: int = 2, max_str: int = 300) -> Any:
    """Shrink a response for fixture storage, preserving structure."""
    if isinstance(obj, dict):
        return {k: trim(v, max_list, max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [trim(v, max_list, max_str) for v in obj[:max_list]]
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "…"
    return obj


class Prober:
    def __init__(self) -> None:
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
        self.results: list[tuple[str, str, str]] = []  # (name, PASS/FAIL/WARN, detail)
        self.ctx: dict[str, Any] = {}  # ids discovered along the way

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.results.append((name, status, detail))
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

    def get_json(self, url: str, params: dict | None = None) -> tuple[int, Any]:
        time.sleep(0.25)  # stay far below 5 req/s
        r = self.client.get(url, params=params)
        try:
            return r.status_code, r.json()
        except json.JSONDecodeError:
            return r.status_code, {"_non_json_body": r.text[:500]}

    def probe(
        self,
        name: str,
        url: str,
        params: dict | None = None,
        require: Callable[[Any], str | None] | None = None,
        fixture: str | None = None,
    ) -> Any:
        """One GET probe. `require` returns an error string or None. Returns the body."""
        try:
            code, body = self.get_json(url, params)
        except httpx.HTTPError as e:
            self.record(name, "FAIL", f"{type(e).__name__}: {e}")
            return None
        if code != 200:
            self.record(name, "FAIL", f"HTTP {code}: {str(body)[:200]}")
            return None
        err = require(body) if require else None
        if err:
            self.record(name, "FAIL", err)
            return None
        if fixture:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            (FIXTURES / f"{fixture}.json").write_text(
                json.dumps(trim(body), indent=1, sort_keys=False) + "\n"
            )
        self.record(name, "PASS")
        return body


def envelope(body: Any) -> str | None:
    """Data API v2 envelope check: {data, pagination{limit,offset,has_more,next_cursor?}}."""
    if not isinstance(body, dict) or "data" not in body or "pagination" not in body:
        got = list(body)[:6] if isinstance(body, dict) else type(body)
        return f"missing envelope keys, got {got}"
    pg = body["pagination"]
    missing = {"limit", "offset", "has_more"} - set(pg)
    return f"pagination missing {missing}" if missing else None


def run_http_probes(p: Prober) -> None:
    # --- Data API v2 ---
    body = p.probe(
        "data-api: GET /v2/leaderboard (month, PNL)",
        f"{DATA_API}/v2/leaderboard",
        {"time_period": "month", "sort_by": "PNL", "limit": 3},
        require=lambda b: (
            envelope(b) or (None if b["data"] and "user_id" in b["data"][0] else "no user_id")
        ),
        fixture="data_api_leaderboard",
    )
    wallet = body["data"][0]["user_id"] if body else None
    p.ctx["wallet"] = wallet

    # v1 leaderboard: spec says it errored on 2026-11-14; verify current behavior.
    code, v1body = p.get_json(f"{DATA_API}/leaderboard", {"window": "month", "limit": 1})
    p.record(
        "data-api: GET /leaderboard (v1, expected broken)",
        "WARN" if code != 200 else "PASS",
        f"HTTP {code}: {str(v1body)[:120]}",
    )

    # cursor pagination: follow next_cursor once, items must differ
    if body and body["pagination"].get("next_cursor"):
        page2 = p.probe(
            "data-api: leaderboard cursor pagination",
            f"{DATA_API}/v2/leaderboard",
            {
                "time_period": "month",
                "sort_by": "PNL",
                "limit": 3,
                "cursor": body["pagination"]["next_cursor"],
            },
            require=lambda b: (
                envelope(b)
                or (
                    None
                    if b["data"] and b["data"][0]["user_id"] != body["data"][0]["user_id"]
                    else "page 2 repeats page 1"
                )
            ),
        )
        if page2:
            p.ctx["lb_page2_rank"] = page2["data"][0]["rank"]

    if wallet:
        trades = p.probe(
            f"data-api: GET /v2/trades?user={wallet[:10]}…",
            f"{DATA_API}/v2/trades",
            {"user": wallet, "limit": 2},
            require=lambda b: (
                envelope(b)
                or (
                    None
                    if b["data"]
                    and {"token_id", "condition_id", "side", "price", "size", "timestamp"}
                    <= set(b["data"][0])
                    else "missing trade fields"
                )
            ),
            fixture="data_api_trades",
        )
        if trades:
            p.ctx["condition_id"] = trades["data"][0]["condition_id"]
            p.ctx["token_id"] = trades["data"][0]["token_id"]
        p.probe(
            "data-api: GET /v2/positions",
            f"{DATA_API}/v2/positions",
            {"user": wallet, "limit": 2},
            require=lambda b: (
                envelope(b)
                or (
                    None
                    if not b["data"]
                    or {"avg_price", "current_size", "status", "outcome_index"} <= set(b["data"][0])
                    else "missing position fields"
                )
            ),
            fixture="data_api_positions",
        )
        p.probe(
            "data-api: GET /v2/activity",
            f"{DATA_API}/v2/activity",
            {"user": wallet, "limit": 3},
            require=lambda b: (
                envelope(b) or (None if not b["data"] or "type" in b["data"][0] else "missing type")
            ),
            fixture="data_api_activity",
        )
        p.probe(
            "data-api: GET /v2/user-pnl",
            f"{DATA_API}/v2/user-pnl",
            {"user": wallet, "interval": "1m"},
            fixture="data_api_user_pnl",
        )

    cond = p.ctx.get("condition_id")
    if cond:
        p.probe(
            "data-api: GET /v2/holders",
            f"{DATA_API}/v2/holders",
            {"condition": cond, "limit": 2},
            fixture="data_api_holders",
        )
        p.probe(
            "data-api: GET /v2/resolutions",
            f"{DATA_API}/v2/resolutions",
            {"condition": cond},
            fixture="data_api_resolutions",
        )

    tok = p.ctx.get("token_id")
    if tok:
        p.probe(
            "data-api: GET /v2/prices-history",
            f"{DATA_API}/v2/prices-history",
            {"token_id": tok, "interval": "1d", "bucket_seconds": 3600},
            require=lambda b: (
                None if isinstance(b, dict) and isinstance(b.get("data"), list) else "no data list"
            ),
            fixture="data_api_prices_history",
        )

    # --- Gamma ---
    mkts = p.probe(
        "gamma: GET /markets",
        f"{GAMMA}/markets",
        {"limit": 2, "closed": "false", "order": "volume24hr", "ascending": "false"},
        require=lambda b: (
            None
            if isinstance(b, list)
            and b
            and {"conditionId", "clobTokenIds", "outcomes"} <= set(b[0])
            else "missing market fields"
        ),
        fixture="gamma_markets",
    )
    p.probe(
        "gamma: GET /events",
        f"{GAMMA}/events",
        {"limit": 1, "closed": "false", "order": "volume24hr", "ascending": "false"},
        require=lambda b: (
            None
            if isinstance(b, list) and b and {"id", "slug", "markets"} <= set(b[0])
            else "missing event fields"
        ),
        fixture="gamma_events",
    )
    # gamma offset pagination
    if mkts:
        p.probe(
            "gamma: /markets offset pagination",
            f"{GAMMA}/markets",
            {
                "limit": 2,
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
                "offset": 2,
            },
            require=lambda b: (
                None
                if isinstance(b, list) and b and b[0]["conditionId"] != mkts[0]["conditionId"]
                else "offset page repeats"
            ),
        )
        gamma_tok = json.loads(mkts[0]["clobTokenIds"])[0]
        p.ctx["gamma_token"] = gamma_tok

        # --- CLOB (reads only) ---
        p.probe(
            "clob: GET /book",
            f"{CLOB}/book",
            {"token_id": gamma_tok},
            require=lambda b: (
                None
                if {"bids", "asks", "tick_size"} <= set(b)
                else f"missing book keys: {list(b)[:8]}"
            ),
            fixture="clob_book",
        )
        p.probe(
            "clob: GET /fee-rate",
            f"{CLOB}/fee-rate",
            {"token_id": gamma_tok},
            require=lambda b: None if "base_fee" in b else f"no base_fee: {b}",
            fixture="clob_fee_rate",
        )
        p.probe(
            "clob: GET /prices-history",
            f"{CLOB}/prices-history",
            {"market": gamma_tok, "interval": "1d", "fidelity": 60},
            require=lambda b: None if "history" in b else f"no history: {list(b)[:6]}",
            fixture="clob_prices_history",
        )

    # --- Polymarket US gateway ---
    usm = p.probe(
        "pm-us: GET /v1/markets",
        f"{US_GW}/v1/markets",
        {"limit": 5, "closed": "false"},
        require=lambda b: (
            None
            if isinstance(b, dict) and b.get("markets") and "slug" in b["markets"][0]
            else "no markets[]"
        ),
        fixture="pmus_markets",
    )
    if usm:
        open_mkts = [m for m in usm["markets"] if m.get("status") == "MARKET_STATUS_OPEN"]
        slug = (open_mkts or usm["markets"])[0]["slug"]
        p.ctx["us_slug"] = slug
        p.probe(
            f"pm-us: GET /v1/markets/{slug}/book",
            f"{US_GW}/v1/markets/{slug}/book",
            require=lambda b: (
                None
                if "marketData" in b and {"bids", "offers"} <= set(b["marketData"])
                else f"book shape: {list(b)[:6]}"
            ),
            fixture="pmus_book",
        )
        p.probe(
            f"pm-us: GET /v1/markets/{slug}/bbo",
            f"{US_GW}/v1/markets/{slug}/bbo",
            fixture="pmus_bbo",
        )
    # settlement on a known-resolved market
    p.probe(
        "pm-us: GET /v1/markets/{slug}/settlement (resolved)",
        f"{US_GW}/v1/markets/aec-nfl-phi-gb-2025-11-10/settlement",
        require=lambda b: None if "settlement" in b else f"no settlement: {b}",
        fixture="pmus_settlement",
    )

    # --- Kalshi ---
    km = p.probe(
        "kalshi: GET /markets",
        f"{KALSHI}/markets",
        {"limit": 2, "status": "open"},
        require=lambda b: (
            None
            if b.get("markets") and {"ticker", "yes_bid_dollars"} <= set(b["markets"][0])
            else "no markets/dollar fields"
        ),
        fixture="kalshi_markets",
    )
    p.probe(
        "kalshi: GET /events",
        f"{KALSHI}/events",
        {"limit": 1, "status": "open"},
        require=lambda b: None if b.get("events") else "no events",
        fixture="kalshi_events",
    )
    if km:
        ticker = km["markets"][0]["ticker"]
        p.probe(
            "kalshi: GET /markets/{ticker}/orderbook",
            f"{KALSHI}/markets/{ticker}/orderbook",
            # current shape: {"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}}
            # (bids only per side; the ask is implied as 1 - opposite bid, per SPEC §3)
            require=lambda b: (
                None if "orderbook" in b or "orderbook_fp" in b else f"shape: {list(b)[:6]}"
            ),
            fixture="kalshi_orderbook",
        )
        p.probe(
            "kalshi: GET /markets/trades",
            f"{KALSHI}/markets/trades",
            {"limit": 2},
            require=lambda b: None if "trades" in b else "no trades",
            fixture="kalshi_trades",
        )

    # --- WebSocket spec (optional source; just confirm the spec document serves) ---
    p.probe(
        "pm-ws: GET asyncapi.json",
        "https://ws-live-v2.polymarket.com/asyncapi.json",
        require=lambda b: None if "asyncapi" in b or "channels" in b else "not asyncapi",
    )


def run_hf_probes(p: Prober) -> None:
    import duckdb

    con = duckdb.connect()
    base = "hf://datasets/moose-code/polymarket-onchain-v1"
    try:
        t0 = time.time()
        q = f"SELECT count(*) FROM '{base}/order_filled/year=2022/*.parquet'"
        n = con.execute(q).fetchone()[0]
        cols = [
            c[0]
            for c in con.execute(
                f"DESCRIBE SELECT * FROM '{base}/order_filled/year=2022/*.parquet' LIMIT 0"
            ).fetchall()
        ]
        need = {"maker", "taker", "makerAssetId", "takerAssetId", "timestamp"}
        need |= {"makerAmountFilled", "takerAmountFilled"}
        missing = need - set(cols)
        if missing:
            p.record("hf: order_filled schema", "FAIL", f"missing {missing}")
        else:
            p.record(
                "hf: order_filled year=2022 count+schema",
                "PASS",
                f"{n} rows, cols={cols} ({time.time() - t0:.1f}s)",
            )
        for tbl, need_cols in [
            ("user_position.parquet", {"user", "avgPrice", "realizedPnl"}),
            ("wallet.parquet", set()),
        ]:
            cols = [
                c[0]
                for c in con.execute(f"DESCRIBE SELECT * FROM '{base}/{tbl}' LIMIT 0").fetchall()
            ]
            missing = need_cols - set(cols)
            if missing:
                p.record(f"hf: {tbl} schema", "FAIL", f"missing {missing}, have {cols}")
            else:
                p.record(f"hf: {tbl} schema", "PASS", f"cols={cols}")
    except Exception as e:
        p.record("hf: duckdb access", "FAIL", f"{type(e).__name__}: {str(e)[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-hf", action="store_true", help="skip DuckDB/HuggingFace probes")
    args = ap.parse_args()

    p = Prober()
    run_http_probes(p)
    if not args.skip_hf:
        run_hf_probes(p)

    fails = [r for r in p.results if r[1] == "FAIL"]
    print(f"\n{len(p.results)} probes: {len(fails)} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
