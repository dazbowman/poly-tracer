"""Wallet selection as-of a refresh date (SPEC §7.2): `skill` vs `naive_pnl`.

Both methods rank only wallets that pass the §7.1 candidate gates and the §7.3
exclusion filters (the spec applies filters to both methods, so even Darren's
original naive-PnL variant excludes obvious market makers and bots).

naive_pnl replicates the leaderboard's finite-window semantics: marked equity
change net of flows over the trailing window,
    pnl = cash_flow_in_window + pos(t1) * mark(t1) - pos(t0) * mark(t0)
computed from fills, with marks from last traded price (the real leaderboard
marks at the midpoint; last-trade is our approximation — DECISIONS d-007).
"""

from __future__ import annotations

import polars as pl

from polycopy.config import Config

EXCLUDED_LABELS = ("market_maker", "bot_arb", "one_hit")


def marks_asof(prices_5m: pl.LazyFrame, ts: int) -> pl.LazyFrame:
    """Last traded price per token strictly before ts (epoch seconds).

    Strict: a bucket at bucket_ts == ts covers [ts, ts+300) — trades after the
    as-of instant (SPEC §0.7).
    """
    return (
        prices_5m.filter(pl.col("bucket_ts") < ts)
        .group_by("token_id")
        .agg(mark=pl.col("last_price").sort_by("bucket_ts").last())
    )


def window_marked_pnl(
    fills: pl.LazyFrame,
    prices_5m: pl.LazyFrame,
    *,
    t0: int,
    t1: int,
) -> pl.DataFrame:
    """Per-wallet leaderboard-style marked PnL over [t0, t1)."""
    f = fills.filter(pl.col("ts") < t1)
    signed_shares = pl.when(pl.col("bought")).then(pl.col("shares")).otherwise(-pl.col("shares"))
    signed_cash = pl.when(pl.col("bought")).then(-pl.col("usdc")).otherwise(pl.col("usdc"))
    per_token = f.group_by("wallet", "token_id").agg(
        pos0=signed_shares.filter(pl.col("ts") < t0).sum().fill_null(0.0),
        pos1=signed_shares.sum().fill_null(0.0),
        cash=signed_cash.filter(pl.col("ts") >= t0).sum().fill_null(0.0),
    )
    m0 = marks_asof(prices_5m, t0).rename({"mark": "m0"})
    m1 = marks_asof(prices_5m, t1).rename({"mark": "m1"})
    out = (
        per_token.join(m0, on="token_id", how="left")
        .join(m1, on="token_id", how="left")
        .with_columns(
            pl.col("m0").fill_null(0.5),  # never-traded-yet tokens: mark at 0.5
            pl.col("m1").fill_null(0.5),
        )
        .with_columns(
            pnl=pl.col("cash") + pl.col("pos1") * pl.col("m1") - pl.col("pos0") * pl.col("m0")
        )
        .group_by("wallet")
        .agg(naive_window_pnl=pl.col("pnl").sum())
    )
    return out.collect(engine="streaming")


def select_wallets(
    features: pl.DataFrame,
    cfg: Config,
    *,
    method: str | None = None,
    n_wallets: int | None = None,
    naive_pnl: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Top-N wallets for one refresh date. Returns rank, wallet, score, method."""
    method = method or cfg.selection.method
    n = n_wallets or cfg.selection.n_wallets
    eligible = features.filter(~pl.col("label").is_in(EXCLUDED_LABELS))
    if method == "skill":
        ranked = (
            eligible.filter(pl.col("skilled") & (pl.col("score") > 0))
            .sort("score", descending=True)
            .head(n)
            .with_columns(sel_score=pl.col("score"))
        )
    elif method == "naive_pnl":
        if naive_pnl is None:
            raise ValueError("naive_pnl selection needs the window-pnl frame")
        ranked = (
            eligible.join(naive_pnl, on="wallet", how="inner")
            .sort("naive_window_pnl", descending=True)
            .head(n)
            .with_columns(sel_score=pl.col("naive_window_pnl"))
        )
    else:
        raise ValueError(f"unknown selection method: {method}")
    return ranked.select(
        pl.col("asof_ts"),
        pl.lit(method).alias("method"),
        pl.int_range(1, ranked.height + 1).alias("rank"),
        "wallet",
        pl.col("sel_score").alias("score"),
    )
