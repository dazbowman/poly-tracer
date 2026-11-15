"""Vectorized per-(wallet, market) PnL — inputs to the skill test (SPEC §7.2).

Needs no sequential state: a market's lifetime PnL is cash flows plus terminal
value, so everything is group-by aggregation over fills_norm:

    pnl_m   = sells_usdc - buys_usdc
              + terminal_yes * payout_yes + terminal_no * (1 - payout_yes)
    r_m     = pnl_m / buys_usdc          (cost-weighted return)

Only RESOLVED markets enter, and only fills at or before the market's
resolution (stray post-resolution fills are anomalies and excluded). This makes
each (wallet, market) row independent of the as-of time, so
market_returns(asof=t) == market_returns() filtered to resolved_s < t —
the features cache precomputes once and filters per refresh date.

Caveat (SPEC §4.6): fills-only reconstruction mis-states PnL for wallets that
split/merge/convert heavily; those are measured by features and filtered out.
"""

from __future__ import annotations

import polars as pl


def fills_is_yes() -> pl.Expr:
    """is_yes from fills_norm columns: canon_dir == +1 iff bought == is_yes."""
    return (pl.col("canon_dir") == 1) == pl.col("bought")


def market_returns(
    fills: pl.LazyFrame,
    markets: pl.LazyFrame,
    *,
    asof_ts: int | None = None,
) -> pl.LazyFrame:
    """Per (wallet, condition_id) lifetime returns on resolved markets.

    `fills` is fills_norm (ts in unix SECONDS); `markets` is the catalog.
    asof_ts (seconds): keep only markets resolved strictly before it. Fills are
    always clamped to the market's resolution time, so no further time filter
    is needed and rows are as-of independent.
    """
    mk = markets.select(
        "condition_id",
        "event_id",
        "category_bucket",
        "payout_yes",
        (pl.col("resolved_ts") // 1000).alias("resolved_s"),
    ).filter(pl.col("payout_yes").is_not_null() & pl.col("resolved_s").is_not_null())
    if asof_ts is not None:
        mk = mk.filter(pl.col("resolved_s") < asof_ts)

    f = (
        fills.join(mk, on="condition_id", how="inner")
        .filter(pl.col("ts") <= pl.col("resolved_s"))
        .with_columns(is_yes=fills_is_yes())
    )
    agg = f.group_by("wallet", "condition_id").agg(
        buys_usdc=pl.col("usdc").filter(pl.col("bought")).sum().fill_null(0.0),
        sells_usdc=pl.col("usdc").filter(~pl.col("bought")).sum().fill_null(0.0),
        terminal_yes=(pl.when(pl.col("bought")).then(pl.col("shares")).otherwise(-pl.col("shares")))
        .filter(pl.col("is_yes"))
        .sum()
        .fill_null(0.0),
        terminal_no=(pl.when(pl.col("bought")).then(pl.col("shares")).otherwise(-pl.col("shares")))
        .filter(~pl.col("is_yes"))
        .sum()
        .fill_null(0.0),
        n_fills=pl.len(),
        first_ts=pl.col("ts").min(),
        last_ts=pl.col("ts").max(),
        taker_share=(pl.col("role") == "taker").mean(),
        event_id=pl.col("event_id").first(),
        category_bucket=pl.col("category_bucket").first(),
        payout_yes=pl.col("payout_yes").first(),
        resolved_s=pl.col("resolved_s").first(),
    )
    return (
        agg.with_columns(
            pnl_usdc=pl.col("sells_usdc")
            - pl.col("buys_usdc")
            + pl.col("terminal_yes") * pl.col("payout_yes")
            + pl.col("terminal_no") * (1.0 - pl.col("payout_yes")),
        )
        .filter(pl.col("buys_usdc") > 0)
        .with_columns(r=pl.col("pnl_usdc") / pl.col("buys_usdc"))
    )
