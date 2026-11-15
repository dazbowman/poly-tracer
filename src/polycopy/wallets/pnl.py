"""Vectorized per-(wallet, market) PnL — inputs to the skill test (SPEC §7.2).

Needs no sequential state: a market's lifetime PnL is cash flows plus terminal
value, so everything is group-by aggregation over fills_norm:

    pnl_m   = sells_usdc - buys_usdc
              + terminal_yes * payout_yes + terminal_no * (1 - payout_yes)
    r_m     = pnl_m / buys_usdc          (cost-weighted return)

Only markets RESOLVED before the as-of time may enter skill features
(SPEC §0.7); unresolved markets have payout null and are excluded.

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
    """Per (wallet, condition_id) lifetime returns on markets resolved before asof_ts.

    `fills` is fills_norm (ts in unix SECONDS); `markets` is the catalog.
    asof_ts (seconds) bounds both the resolution time and the fills used.
    """
    mk = markets.select(
        "condition_id",
        "event_id",
        "category_bucket",
        "payout_yes",
        (pl.col("resolved_ts") // 1000).alias("resolved_s"),
    ).filter(pl.col("payout_yes").is_not_null())
    if asof_ts is not None:
        mk = mk.filter(pl.col("resolved_s") < asof_ts)
        fills = fills.filter(pl.col("ts") < asof_ts)

    f = fills.with_columns(is_yes=fills_is_yes())
    agg = f.group_by("wallet", "condition_id").agg(
        buys_usdc=pl.col("usdc").filter(pl.col("bought")).sum(),
        sells_usdc=pl.col("usdc").filter(~pl.col("bought")).sum(),
        terminal_yes=(pl.when(pl.col("bought")).then(pl.col("shares")).otherwise(-pl.col("shares")))
        .filter(pl.col("is_yes"))
        .sum(),
        terminal_no=(pl.when(pl.col("bought")).then(pl.col("shares")).otherwise(-pl.col("shares")))
        .filter(~pl.col("is_yes"))
        .sum(),
        n_fills=pl.len(),
        first_ts=pl.col("ts").min(),
        last_ts=pl.col("ts").max(),
        taker_share=(pl.col("role") == "taker").mean(),
    )
    return (
        agg.join(mk, on="condition_id", how="inner")
        .with_columns(
            pl.col("buys_usdc").fill_null(0.0),
            pl.col("sells_usdc").fill_null(0.0),
            pl.col("terminal_yes").fill_null(0.0),
            pl.col("terminal_no").fill_null(0.0),
        )
        .with_columns(
            pnl_usdc=pl.col("sells_usdc")
            - pl.col("buys_usdc")
            + pl.col("terminal_yes") * pl.col("payout_yes")
            + pl.col("terminal_no") * (1.0 - pl.col("payout_yes")),
        )
        .filter(pl.col("buys_usdc") > 0)
        .with_columns(r=pl.col("pnl_usdc") / pl.col("buys_usdc"))
    )
