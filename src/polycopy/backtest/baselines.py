"""B0: market-matched random taker buys (SPEC §8.2).

For each strategy trade, sample `n_per_trade` random taker-buy opportunities in
the same category, price bucket (±2c), time-to-resolution bucket and entry week;
buy with the same notional, same fee schedule, hold to resolution. Measures
"the price is right minus costs" — the expectation the strategy must beat.

The opportunity pool is built from hourly aggregates of prices_5m joined to the
catalog; only resolved markets qualify (payout known). The pool row's
taker_buy_vwap stands in for the §7.7 base price; the penalty+slippage model
and fees are applied identically to strategy trades.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from polycopy.sim.fees import pm_us_fee

TTR_BUCKET_EDGES_H = [0, 24, 168, 720, 1e9]  # <1d, 1-7d, 7-30d, >30d
WEEK_S = 7 * 86_400


def ttr_bucket(hours: pl.Expr) -> pl.Expr:
    return (
        pl.when(hours < 24).then(0).when(hours < 168).then(1).when(hours < 720).then(2).otherwise(3)
    )


def build_opportunity_pool(
    prices_hourly: pl.LazyFrame,
    markets: pl.LazyFrame,
    *,
    min_taker_buy_usdc: float = 100.0,
) -> pl.LazyFrame:
    """Pool of (token, hour) taker-buy cells on markets with known payouts.

    prices_hourly: token_id, bucket_ts (s), taker_buy_vwap, taker_buy_usdc,
    volume_usdc. markets: catalog columns.
    """
    yes = markets.select(
        token_id=pl.col("yes_token_id"),
        condition_id="condition_id",
        event_id="event_id",
        category=pl.col("category_bucket"),
        end_s=pl.col("end_ts") // 1000,
        resolved_s=pl.col("resolved_ts") // 1000,
        payout_token=pl.col("payout_yes"),
    )
    no = markets.select(
        token_id=pl.col("no_token_id"),
        condition_id="condition_id",
        event_id="event_id",
        category=pl.col("category_bucket"),
        end_s=pl.col("end_ts") // 1000,
        resolved_s=pl.col("resolved_ts") // 1000,
        payout_token=1.0 - pl.col("payout_yes"),
    )
    tok = pl.concat([yes, no]).filter(pl.col("payout_token").is_not_null())
    return (
        prices_hourly.filter(pl.col("taker_buy_usdc") >= min_taker_buy_usdc)
        .join(tok, on="token_id", how="inner")
        .filter(pl.col("bucket_ts") < pl.col("resolved_s"))
        .with_columns(
            ttr_b=ttr_bucket((pl.col("end_s") - pl.col("bucket_ts")) / 3600),
            week=pl.col("bucket_ts") // WEEK_S,
            price_c=(pl.col("taker_buy_vwap") * 100).round(0),
        )
    )


def b0_sample(
    strategy_trades: pl.DataFrame,
    pool: pl.DataFrame,
    *,
    n_per_trade: int = 20,
    price_tol_c: float = 2.0,
    theta: float = 0.095,
    penalty: float = 0.01,
    seed: int = 1729,
) -> pl.DataFrame:
    """strategy_trades: entry_ts (s), entry_price, notional, category, end_s, event_id.

    Returns one row per sampled baseline trade with net pnl after fees.
    """
    rng = np.random.default_rng(seed)
    out_rows = []
    pool_by_key = pool.partition_by(["category", "ttr_b", "week"], as_dict=True)
    for i, t in enumerate(strategy_trades.iter_rows(named=True)):
        ttr_h = (t["end_s"] - t["entry_ts"]) / 3600
        tb = 0 if ttr_h < 24 else 1 if ttr_h < 168 else 2 if ttr_h < 720 else 3
        key = (t["category"], tb, t["entry_ts"] // WEEK_S)
        cells = pool_by_key.get(key)
        if cells is None:
            continue
        m = cells.filter((pl.col("taker_buy_vwap") - t["entry_price"]).abs() <= price_tol_c / 100)
        if m.height == 0:
            continue
        take = min(n_per_trade, m.height)
        idx = rng.choice(m.height, size=take, replace=m.height < n_per_trade)
        sampled = m[np.sort(idx)]
        for s in sampled.iter_rows(named=True):
            price = min(s["taker_buy_vwap"] + penalty, 0.999)
            shares = t["notional"] / price
            fee = pm_us_fee(shares, price, theta)
            pnl = shares * s["payout_token"] - t["notional"] - fee
            out_rows.append(
                {
                    "strategy_trade_idx": i,
                    "event_id": s["event_id"],
                    "token_id": s["token_id"],
                    "entry_ts": s["bucket_ts"],
                    "price": price,
                    "cost": t["notional"],
                    "fee": fee,
                    "net_pnl": pnl,
                    "payout": s["payout_token"],
                }
            )
    return pl.DataFrame(out_rows)
