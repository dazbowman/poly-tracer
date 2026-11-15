"""B0 validation: in a perfectly calibrated synthetic world the baseline must
earn about minus-costs (fees + penalty), never a positive edge."""

import numpy as np
import polars as pl

from polycopy.backtest.baselines import b0_sample, build_opportunity_pool

WEEK = 7 * 86_400
T0 = 1_700_000_000 - (1_700_000_000 % WEEK)


def calibrated_world(n_markets=400, seed=3):
    """Markets whose price equals the true probability; outcomes drawn from it."""
    rng = np.random.default_rng(seed)
    probs = rng.uniform(0.2, 0.8, n_markets)
    outcomes = (rng.random(n_markets) < probs).astype(float)
    mk = pl.DataFrame(
        {
            "condition_id": [f"c{i}" for i in range(n_markets)],
            "event_id": [f"e{i}" for i in range(n_markets)],
            "category_bucket": ["politics"] * n_markets,
            "yes_token_id": [f"c{i}-y" for i in range(n_markets)],
            "no_token_id": [f"c{i}-n" for i in range(n_markets)],
            "end_ts": [(T0 + 5 * 86_400) * 1000] * n_markets,
            "resolved_ts": [(T0 + 5 * 86_400) * 1000] * n_markets,
            "payout_yes": outcomes,
        }
    )
    prices = pl.DataFrame(
        {
            "token_id": [f"c{i}-y" for i in range(n_markets)],
            "bucket_ts": [T0 + 3600] * n_markets,
            "taker_buy_vwap": probs,
            "taker_buy_usdc": [1_000.0] * n_markets,
            "volume_usdc": [2_000.0] * n_markets,
        }
    )
    return mk, prices, probs


def test_pool_excludes_unresolved_and_post_resolution():
    mk, prices, _ = calibrated_world(10)
    mk = mk.with_columns(
        payout_yes=pl.when(pl.col("condition_id") == "c0").then(None).otherwise("payout_yes")
    )
    late = pl.DataFrame(
        {
            "token_id": ["c1-y"],
            "bucket_ts": [T0 + 10 * 86_400],  # after resolution
            "taker_buy_vwap": [0.5],
            "taker_buy_usdc": [1_000.0],
            "volume_usdc": [2_000.0],
        }
    )
    pool = build_opportunity_pool(pl.concat([prices, late]).lazy(), mk.lazy()).collect()
    assert "c0-y" not in pool["token_id"].to_list()  # unresolved market
    assert pool.filter(pl.col("bucket_ts") > T0 + 5 * 86_400).height == 0


def test_calibrated_world_b0_loses_about_fees():
    # 4000 markets so one lucky outcome draw (binomial noise ~1.8% at 800
    # samples) cannot flip the ~2.4% cost drag positive; seeds keep it exact
    mk, prices, _probs = calibrated_world(4000)
    pool = build_opportunity_pool(prices.lazy(), mk.lazy()).collect()
    # strategy trades spread over the price range, same week/category/ttr
    trades = pl.DataFrame(
        {
            "entry_ts": [T0 + 3600] * 40,
            "entry_price": np.linspace(0.25, 0.75, 40),
            "notional": [200.0] * 40,
            "category": ["politics"] * 40,
            "end_s": [T0 + 5 * 86_400] * 40,
            "event_id": [f"s{i}" for i in range(40)],
        }
    )
    b0 = b0_sample(trades, pool, n_per_trade=20, seed=11)
    assert b0.height > 400
    roi = b0["net_pnl"].sum() / b0["cost"].sum()
    # expected drag: 1c penalty on ~0.5 price (~2%) + PM-US fee (~2% near 0.5 on cost)
    assert roi < 0, "calibrated market cannot be beaten by random taker buys"
    assert -0.12 < roi < -0.005, f"drag should be a few percent, got {roi:.3f}"


def test_b0_matches_price_bucket():
    mk, prices, _ = calibrated_world(300)
    pool = build_opportunity_pool(prices.lazy(), mk.lazy()).collect()
    trades = pl.DataFrame(
        {
            "entry_ts": [T0 + 3600],
            "entry_price": [0.30],
            "notional": [100.0],
            "category": ["politics"],
            "end_s": [T0 + 5 * 86_400],
            "event_id": ["s0"],
        }
    )
    b0 = b0_sample(trades, pool, n_per_trade=50, seed=2)
    assert b0.height > 0
    # all samples within +-2c of 0.30 (before the 1c penalty)
    assert ((b0["price"] - 0.01 - 0.30).abs() <= 0.0201).all()
