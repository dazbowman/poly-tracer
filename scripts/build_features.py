"""Build the weekly as-of features cache + naive window PnL for selection.

Usage: uv run python scripts/build_features.py [--start 2024-06-24] [--end 2026-04-20]
Writes data/market_returns/bucket_*.parquet (one-time precompute), then per
Monday 00:00 UTC data/features/asof=<date>.parquet (candidate features+labels)
and data/features/naive=<date>.parquet (leaderboard-style window PnL).

Requires: markets.parquet (Gamma crawl), chain_condition_map.parquet,
fills_norm/, prices_5m/, wallet_events.parquet.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

import polars as pl

from polycopy import paths
from polycopy.config import Config
from polycopy.wallets.features import build_features_asof
from polycopy.wallets.pnl import market_returns
from polycopy.wallets.select import window_marked_pnl

log = logging.getLogger("build_features")
DAY_S = 86_400
RETS_DIR = paths.DATA / "market_returns"


def build_market_returns(fills: pl.LazyFrame, markets: pl.LazyFrame) -> None:
    """One-time precompute of as-of-independent market returns (pnl.py docstring).

    Partitioned by first wallet hex char so each group-by holds ~1/4 of the
    170M-leg history; atomic per-bucket writes make a killed run resumable.
    """
    RETS_DIR.mkdir(parents=True, exist_ok=True)
    for g in ("0123", "4567", "89ab", "cdef"):
        out = RETS_DIR / f"bucket_{g}.parquet"
        if out.exists():
            continue
        t = time.time()
        sub = fills.filter(pl.col("wallet").str.slice(2, 1).is_in(list(g)))
        tmp = out.with_suffix(".tmp")
        market_returns(sub, markets).sink_parquet(tmp, compression="zstd")
        tmp.rename(out)
        log.info("market_returns bucket %s done (%.0fs)", g, time.time() - t)


def mondays(start: str, end: str) -> list[int]:
    d = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    d += timedelta(days=(7 - d.weekday()) % 7)  # next Monday (or same day)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    out = []
    while d <= end_dt:
        out.append(int(d.timestamp()))
        d += timedelta(days=7)
    return out


def load_markets() -> pl.LazyFrame:
    """Gamma catalog enriched with chain payouts (chain wins when gamma is null)."""
    gamma = pl.scan_parquet(paths.MARKETS_PARQUET)
    chain = pl.scan_parquet(paths.DATA / "chain_condition_map.parquet").select(
        "condition_id", chain_payout=pl.col("payout_yes")
    )
    return gamma.join(chain, on="condition_id", how="left").with_columns(
        payout_yes=pl.coalesce(pl.col("payout_yes"), pl.col("chain_payout"))
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-06-24")  # first refresh before Train start
    ap.add_argument("--end", default="2026-04-20")
    ap.add_argument("--skip-naive", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = Config.load(paths.REPO / "configs" / "default.yaml")
    out_dir = paths.DATA / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    markets = load_markets()
    fills = pl.scan_parquet(paths.FILLS_DIR / "*.parquet")
    events = pl.scan_parquet(paths.DATA / "wallet_events.parquet")
    prices = pl.scan_parquet(paths.PRICES_DIR / "*.parquet")

    build_market_returns(fills, markets)
    mkt_rets_all = pl.scan_parquet(RETS_DIR / "bucket_*.parquet")

    dates = mondays(args.start, args.end)
    log.info("%d weekly refresh dates", len(dates))
    t0 = time.time()
    for i, asof in enumerate(dates, 1):
        tag = datetime.fromtimestamp(asof, tz=UTC).strftime("%Y-%m-%d")
        f_out = out_dir / f"asof={tag}.parquet"
        n_out = out_dir / f"naive={tag}.parquet"
        if not f_out.exists():
            df = build_features_asof(
                fills, events, markets, asof_ts=asof, cfg=cfg, mkt_rets_all=mkt_rets_all
            )
            df.write_parquet(f_out, compression="zstd")
            log.info(
                "[%d/%d] %s: %d candidates (%.0fs)", i, len(dates), tag, df.height, time.time() - t0
            )
        if not args.skip_naive and not n_out.exists():
            # selection inner-joins naive pnl with the gate-passing candidates
            # (select.py), so only their window pnl is ever read — restricting
            # the (wallet, token) position group-by to them bounds memory
            cand = pl.scan_parquet(f_out).select("wallet").unique()
            naive = window_marked_pnl(
                fills.join(cand, on="wallet", how="semi"), prices, t0=asof - 30 * DAY_S, t1=asof
            )
            naive.write_parquet(n_out, compression="zstd")
            log.info(
                "[%d/%d] %s: naive pnl %d wallets (%.0fs)",
                i,
                len(dates),
                tag,
                naive.height,
                time.time() - t0,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
