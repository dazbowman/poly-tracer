"""Per-wallet features as-of a timestamp (SPEC §6 wallet_features_asof, §7.1-7.3).

Point-in-time discipline (SPEC §0.7): only fills with ts < asof enter, and only
markets with resolved_ts < asof count as resolved. The lookback window applies
to the market's RESOLUTION date for skill features (a market resolving inside
the window contributes its whole fill history), and to fill timestamps for
activity features (volume, maker share, cadence).
"""

from __future__ import annotations

import numpy as np
import polars as pl

from polycopy.config import Config
from polycopy.wallets.pnl import market_returns
from polycopy.wallets.skill import benjamini_hochberg, shrunk_lcb_score, sign_flip_pvalue

DAY_S = 86_400


def activity_features(fills_lb: pl.LazyFrame, markets: pl.LazyFrame) -> pl.LazyFrame:
    """Features from lookback-window fills (ts already filtered by caller)."""
    cat = markets.select("condition_id", "category_bucket")
    f = fills_lb.join(cat, on="condition_id", how="left")
    base = f.group_by("wallet").agg(
        volume_usdc=pl.col("usdc").sum(),
        maker_share=(pl.col("usdc").filter(pl.col("role") == "maker").sum() / pl.col("usdc").sum()),
        n_fills=pl.len(),
        last_trade_ts=pl.col("ts").max(),
        first_trade_ts=pl.col("ts").min(),
        short_crypto_share=(
            pl.col("usdc").filter(pl.col("category_bucket") == "crypto_short").sum()
            / pl.col("usdc").sum()
        ),
        median_intertrade_s=pl.col("ts").sort().diff().median(),
        active_hours=((pl.col("ts") % DAY_S) // 3600).n_unique(),
    )
    # category mix as separate aggregation (volume share per bucket, top-3 string)
    mix = (
        f.group_by("wallet", "category_bucket")
        .agg(v=pl.col("usdc").sum())
        .sort("v", descending=True)
        .group_by("wallet", maintain_order=True)
        .agg(
            category_top=pl.col("category_bucket").first(),
            category_top_share=(pl.col("v").first() / pl.col("v").sum()),
        )
    )
    # two-sided share, day-bucketed approximation of "within 24h" (SPEC §7.3):
    # per (wallet, token, utc-day): 2*min(buy_usdc, sell_usdc) is two-sided notional
    two = (
        fills_lb.group_by("wallet", "token_id", (pl.col("ts") // DAY_S).alias("day"))
        .agg(
            b=pl.col("usdc").filter(pl.col("bought")).sum().fill_null(0.0),
            s=pl.col("usdc").filter(~pl.col("bought")).sum().fill_null(0.0),
        )
        .group_by("wallet")
        .agg(
            two_sided_share=(
                2.0
                * pl.min_horizontal(pl.col("b"), pl.col("s")).sum()
                / (pl.col("b") + pl.col("s")).sum()
            )
        )
    )
    # median holding span per condition (first to last touch), hours; and the
    # median per-market buy cost in the window (conviction-ratio denominator, §7.4 S1)
    hold = (
        fills_lb.group_by("wallet", "condition_id")
        .agg(
            span_h=(pl.col("ts").max() - pl.col("ts").min()) / 3600.0,
            mkt_cost=pl.col("usdc").filter(pl.col("bought")).sum(),
        )
        .group_by("wallet")
        .agg(
            median_hold_hours=pl.col("span_h").median(),
            median_market_cost=pl.col("mkt_cost").drop_nulls().median(),
        )
    )
    return (
        base.join(mix, on="wallet", how="left")
        .join(two, on="wallet", how="left")
        .join(hold, on="wallet", how="left")
    )


def skill_features(mkt_rets: pl.DataFrame, *, n0: int, seed: int = 1729) -> pl.DataFrame:
    """Per-wallet skill stats from per-market returns (resolved in lookback)."""
    grouped = mkt_rets.group_by("wallet").agg(
        n_resolved_markets=pl.col("condition_id").n_unique(),
        resolved_cost_usdc=pl.col("buys_usdc").sum(),
        resolved_pnl_usdc=pl.col("pnl_usdc").sum(),
        top_market_pnl_share=(pl.col("pnl_usdc").abs().max() / pl.col("pnl_usdc").abs().sum()),
        # canonical order: the exact-flip p-value pairs rng draws with array
        # positions, so group-internal row order must not leak in
        costs=pl.col("buys_usdc").sort_by(pl.col("condition_id")),
        rets=pl.col("r").sort_by(pl.col("condition_id")),
    )
    pvals, scores = [], []
    for wallet, costs, rets in zip(
        grouped["wallet"], grouped["costs"], grouped["rets"], strict=True
    ):
        c = costs.to_numpy().astype(float)
        r = rets.to_numpy().astype(float)
        # per-wallet RNG stream: p-values must not depend on group iteration
        # order (polars group_by order is unstable)
        wallet_seed = int.from_bytes(wallet[-8:].encode(), "little") % (2**31)
        rng = np.random.default_rng([seed, wallet_seed])
        pvals.append(sign_flip_pvalue(c, r, rng=rng))
        scores.append(shrunk_lcb_score(c, r, n0=n0))
    return grouped.drop("costs", "rets").with_columns(
        skill_pvalue=pl.Series(pvals, dtype=pl.Float64),
        score=pl.Series(scores, dtype=pl.Float64),
        roi_cost_weighted=pl.col("resolved_pnl_usdc") / pl.col("resolved_cost_usdc"),
    )


def classify(df: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    """Apply SPEC §7.3 exclusion filters and label wallets."""
    fc = cfg.filters
    vol_to_pnl = pl.col("volume_usdc") / pl.col("resolved_pnl_usdc").abs().clip(1e-9)
    is_mm = (
        (pl.col("two_sided_share") > fc.mm_two_sided_share_max)
        | (pl.col("split_merge_share") > fc.mm_split_merge_share_max)
        | (vol_to_pnl > fc.mm_volume_to_abs_pnl_max)
    )
    is_bot = (pl.col("short_crypto_share") > fc.bot_short_crypto_share_max) | (
        (pl.col("median_intertrade_s") < 10) & (pl.col("active_hours") >= 20)
    )
    is_onehit = pl.col("top_market_pnl_share") > fc.one_hit_top_market_share_max
    return df.with_columns(
        label=pl.when(is_mm.fill_null(False))
        .then(pl.lit("market_maker"))
        .when(is_bot.fill_null(False))
        .then(pl.lit("bot_arb"))
        .when(is_onehit.fill_null(False))
        .then(pl.lit("one_hit"))
        .when(pl.col("skilled"))
        .then(pl.lit("skilled_directional"))
        .otherwise(pl.lit("unskilled"))
    )


def build_features_asof(
    fills: pl.LazyFrame,
    wallet_events: pl.LazyFrame,
    markets: pl.LazyFrame,
    *,
    asof_ts: int,
    cfg: Config,
    mkt_rets_all: pl.LazyFrame | None = None,
) -> pl.DataFrame:
    """One row per wallet meeting the §7.1 candidate gates, with label.

    mkt_rets_all: optional precomputed market_returns(fills, markets) — rows are
    as-of independent (fills clamped to resolution), so passing it is exactly
    equivalent and much faster across many refresh dates.
    """
    lb_start = asof_ts - cfg.universe.lookback_days * DAY_S
    fills_lb = fills.filter((pl.col("ts") < asof_ts) & (pl.col("ts") >= lb_start))

    # skill inputs: markets resolved inside [lb_start, asof), whole fill history
    rets_lazy = mkt_rets_all if mkt_rets_all is not None else market_returns(fills, markets)
    rets_lazy = rets_lazy.filter(
        (pl.col("resolved_s") < asof_ts) & (pl.col("resolved_s") >= lb_start)
    )
    act = activity_features(fills_lb, markets).collect()
    # The sign-flip loop is per-wallet python+numpy, so restrict it to wallets
    # that can pass the §7.1 gates below (per-wallet RNG seeds and the post-gate
    # BH make this a pure speedup: kept rows are byte-identical)
    gate_wallets = (
        act.lazy()
        .join(rets_lazy.group_by("wallet").agg(nrm=pl.len()), on="wallet", how="inner")
        .filter(
            (pl.col("nrm") >= cfg.universe.min_resolved_markets)
            & (pl.col("volume_usdc") >= cfg.universe.min_volume_usd)
            & (pl.col("last_trade_ts") >= asof_ts - cfg.universe.active_within_days * DAY_S)
        )
        .select("wallet")
    )
    rets = rets_lazy.join(gate_wallets, on="wallet", how="semi").collect()
    ev = (
        wallet_events.filter((pl.col("ts") >= lb_start) & (pl.col("ts") < asof_ts))
        .filter(pl.col("kind").is_in(["split", "merge", "convert"]))
        .group_by("wallet")
        .agg(split_merge_usdc=pl.col("amount_usdc").sum())
        .collect()
    )
    sk = skill_features(rets, n0=cfg.selection.shrink_n0, seed=cfg.seed)

    df = (
        act.join(sk, on="wallet", how="left")
        .join(ev, on="wallet", how="left")
        .with_columns(
            pl.col("split_merge_usdc").fill_null(0.0),
            pl.col("n_resolved_markets").fill_null(0),
        )
        .with_columns(
            split_merge_share=pl.col("split_merge_usdc")
            / (pl.col("split_merge_usdc") + pl.col("volume_usdc")),
            trades_per_day=pl.col("n_fills") / (cfg.universe.lookback_days),
        )
    )
    # §7.1 candidate gates
    df = df.filter(
        (pl.col("n_resolved_markets") >= cfg.universe.min_resolved_markets)
        & (pl.col("volume_usdc") >= cfg.universe.min_volume_usd)
        & (pl.col("last_trade_ts") >= asof_ts - cfg.universe.active_within_days * DAY_S)
    )
    # FDR across the candidates that reached this point
    if df.height:
        mask = benjamini_hochberg(
            df["skill_pvalue"].fill_null(1.0).to_numpy(), q=cfg.selection.fdr_q
        )
        df = df.with_columns(skilled=pl.Series(mask))
        # BH threshold as an implied q-value proxy: smallest q at which p passes
        n = df.height
        order = df["skill_pvalue"].fill_null(1.0).rank(method="ordinal")
        df = df.with_columns(skill_qvalue=(pl.col("skill_pvalue") * n / order).clip(0.0, 1.0))
    else:
        df = df.with_columns(
            skilled=pl.Series([], dtype=pl.Boolean), skill_qvalue=pl.Series([], dtype=pl.Float64)
        )
    df = classify(df, cfg)
    return df.with_columns(asof_ts=pl.lit(asof_ts, dtype=pl.Int64)).sort(
        "score", descending=True, nulls_last=True
    )
