"""Synthetic-world test of build_features_asof: gates, skill, MM filter, labels."""

import numpy as np
import polars as pl
import pytest

from polycopy.config import Config
from polycopy.wallets.features import build_features_asof

DAY = 86_400
ASOF = 1_700_000_000  # arbitrary epoch seconds
CFG = Config().model_copy(
    update={
        "universe": Config().universe.model_copy(
            update={"min_resolved_markets": 5, "min_volume_usd": 1_000}
        )
    }
)

FILL_COLS = [
    "ts",
    "wallet",
    "counterparty",
    "condition_id",
    "token_id",
    "canon_dir",
    "shares",
    "usdc",
    "price_yes_equiv",
    "bought",
    "role",
]


def _mk_markets(n: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "condition_id": [f"c{i}" for i in range(n)],
            "event_id": [f"e{i}" for i in range(n)],
            "category_bucket": ["politics"] * n,
            # resolved inside the lookback, alternating payouts
            "resolved_ts": [(ASOF - (i + 2) * DAY) * 1000 for i in range(n)],
            "payout_yes": [1.0 if i % 2 == 0 else 0.0 for i in range(n)],
        }
    )


def _buy(w, cond, ts, price, usdc, yes=True, role="taker"):
    shares = usdc / price
    return dict(
        zip(
            FILL_COLS,
            [
                ts,
                w,
                "cp",
                cond,
                f"{cond}-{'y' if yes else 'n'}",
                1 if yes else -1,
                shares,
                usdc,
                price if yes else 1 - price,
                True,
                role,
            ],
            strict=True,
        )
    )


def _sell(w, cond, ts, price, usdc, yes=True, role="maker"):
    shares = usdc / price
    return dict(
        zip(
            FILL_COLS,
            [
                ts,
                w,
                "cp",
                cond,
                f"{cond}-{'y' if yes else 'n'}",
                -1 if yes else 1,
                shares,
                usdc,
                price if yes else 1 - price,
                False,
                role,
            ],
            strict=True,
        )
    )


def test_pipeline_gates_skill_and_mm_label():
    n_mkts = 12
    markets = _mk_markets(n_mkts)
    rows = []
    # sharp: buys the winning side of every market at 0.60, 500 USDC each
    for i in range(n_mkts):
        ts = ASOF - (i + 3) * DAY
        wins_yes = i % 2 == 0
        rows.append(_buy("sharp", f"c{i}", ts, 0.60, 500, yes=wins_yes))
    # churner: two-sided same-token same-day churn, flat PnL, huge volume
    for i in range(n_mkts):
        ts = ASOF - (i + 3) * DAY
        rows.append(_buy("churner", f"c{i}", ts, 0.50, 5_000))
        rows.append(_sell("churner", f"c{i}", ts + 600, 0.50, 5_000))
    # minnow: tiny volume, few markets -> fails §7.1 gates
    rows.append(_buy("minnow", "c0", ASOF - 3 * DAY, 0.5, 50))
    fills = pl.DataFrame(rows).lazy()
    events = pl.DataFrame(
        {
            "kind": ["split"],
            "wallet": ["nobody"],
            "condition_id": ["c0"],
            "ts": [ASOF - DAY],
            "amount_usdc": [1.0],
            "extra": [None],
        }
    ).lazy()

    df = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=CFG)
    assert set(df["wallet"].to_list()) == {"sharp", "churner"}  # minnow gated out

    sharp = df.filter(pl.col("wallet") == "sharp").row(0, named=True)
    # every market: bought winner at 0.60 -> r = (1-0.6)/0.6
    assert sharp["roi_cost_weighted"] == pytest.approx(0.4 / 0.6)
    assert sharp["skill_pvalue"] < 0.01
    assert sharp["label"] == "skilled_directional"
    assert sharp["n_resolved_markets"] == n_mkts

    churn = df.filter(pl.col("wallet") == "churner").row(0, named=True)
    assert churn["two_sided_share"] == pytest.approx(1.0)
    assert churn["label"] == "market_maker"


def test_point_in_time_no_future_resolution():
    markets = pl.DataFrame(
        {
            "condition_id": ["c0"],
            "event_id": ["e0"],
            "category_bucket": ["sports"],
            "resolved_ts": [(ASOF + 5 * DAY) * 1000],  # resolves AFTER asof
            "payout_yes": [1.0],
        }
    )
    fills = pl.DataFrame(
        [_buy("w", "c0", ASOF - 2 * DAY, 0.5, 2_000)] * 40  # volume gate passes
    ).lazy()
    events = pl.DataFrame(
        schema={
            "kind": pl.Utf8,
            "wallet": pl.Utf8,
            "condition_id": pl.Utf8,
            "ts": pl.Int64,
            "amount_usdc": pl.Float64,
            "extra": pl.Utf8,
        }
    ).lazy()
    df = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=CFG)
    # the only market is unresolved as of ASOF -> zero resolved markets -> gated out
    assert df.height == 0


def test_lookback_applies_to_resolution_not_entry():
    cfg = CFG
    lb_start = ASOF - cfg.universe.lookback_days * DAY
    markets = _mk_markets(6)
    rows = []
    for i in range(6):
        # entries BEFORE the lookback window, resolutions inside it
        rows.append(_buy("w", f"c{i}", lb_start - 30 * DAY, 0.60, 500, yes=(i % 2 == 0)))
        # plus in-window adds 1h before each market's resolution (fills after
        # resolution can't exist on-chain and are clamped out by market_returns)
        resolved_s = (ASOF - (i + 2) * DAY) - 3600
        rows.append(_buy("w", f"c{i}", resolved_s, 0.60, 200, yes=(i % 2 == 0)))
    fills = pl.DataFrame(rows).lazy()
    events = pl.DataFrame(
        schema={
            "kind": pl.Utf8,
            "wallet": pl.Utf8,
            "condition_id": pl.Utf8,
            "ts": pl.Int64,
            "amount_usdc": pl.Float64,
            "extra": pl.Utf8,
        }
    ).lazy()
    cfg2 = cfg.model_copy(
        update={"universe": cfg.universe.model_copy(update={"min_volume_usd": 500})}
    )
    df = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=cfg2)
    w = df.row(0, named=True)
    # all 6 markets count (resolved in window), using full history cost 700 each
    assert w["n_resolved_markets"] == 6
    assert w["resolved_cost_usdc"] == pytest.approx(6 * 700)


def test_seeded_determinism():
    markets = _mk_markets(8)
    rows = [_buy("w", f"c{i}", ASOF - (i + 3) * DAY, 0.55, 400, yes=(i % 2 == 0)) for i in range(8)]
    fills = pl.DataFrame(rows).lazy()
    events = pl.DataFrame(
        schema={
            "kind": pl.Utf8,
            "wallet": pl.Utf8,
            "condition_id": pl.Utf8,
            "ts": pl.Int64,
            "amount_usdc": pl.Float64,
            "extra": pl.Utf8,
        }
    ).lazy()
    a = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=CFG)
    b = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=CFG)
    assert np.allclose(a["skill_pvalue"].to_numpy(), b["skill_pvalue"].to_numpy())


def test_precomputed_mkt_rets_path_is_equivalent():
    from polycopy.wallets.pnl import market_returns

    markets = _mk_markets(10)
    rows = []
    for i in range(10):
        rows.append(_buy("w", f"c{i}", ASOF - (i + 3) * DAY, 0.55, 600, yes=(i % 2 == 0)))
        rows.append(_buy("v", f"c{i}", ASOF - (i + 3) * DAY, 0.50, 800, yes=(i % 3 == 0)))
    fills = pl.DataFrame(rows).lazy()
    events = pl.DataFrame(
        schema={
            "kind": pl.Utf8,
            "wallet": pl.Utf8,
            "condition_id": pl.Utf8,
            "ts": pl.Int64,
            "amount_usdc": pl.Float64,
            "extra": pl.Utf8,
        }
    ).lazy()
    pre = market_returns(fills, markets.lazy()).collect().lazy()
    a = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=CFG)
    b = build_features_asof(fills, events, markets.lazy(), asof_ts=ASOF, cfg=CFG, mkt_rets_all=pre)
    assert a.sort("wallet").equals(b.sort("wallet"))
