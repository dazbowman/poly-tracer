"""Shared synthetic-world builders for engine and leakage tests."""

import numpy as np

from polycopy.backtest.engine import Engine, MarketMeta, SimData, WalletFills
from polycopy.config import Config
from polycopy.sim.fills_backtest import TokenTape

T0 = 1_700_000_000 - (1_700_000_000 % 86_400)  # day-aligned start
H = 3600


def flat_tape(t_start, t_end, price, bar_usdc=1_000.0):
    ts = np.arange(t_start, t_end, 300, dtype=np.int64)
    n = len(ts)
    return TokenTape(
        bucket_ts=ts,
        taker_buy_vwap=np.full(n, price),
        taker_sell_vwap=np.full(n, max(price - 0.01, 0.001)),
        taker_buy_usdc=np.full(n, bar_usdc / 2),
        volume_usdc=np.full(n, bar_usdc),
        last_price=np.full(n, price),
    )


def market(cond="c1", event="e1", end_h=24 * 10, resolved_h=24 * 12, payout=1.0):
    return MarketMeta(
        condition_id=cond,
        event_id=event,
        yes_token=f"{cond}-y",
        no_token=f"{cond}-n",
        category="politics",
        neg_risk=False,
        end_ts=T0 + end_h * H,
        resolved_ts=(T0 + resolved_h * H) if resolved_h else None,
        payout_yes=payout,
    )


def wallet_buys(cond, ts_list, price, usdc, yes=True):
    n = len(ts_list)
    return WalletFills(
        ts=np.array(ts_list, dtype=np.int64),
        condition_id=[cond] * n,
        is_yes=np.full(n, yes),
        bought=np.full(n, True),
        shares=np.array([u / price for u in [usdc] * n]),
        usdc=np.full(n, float(usdc)),
    )


def base_cfg(**signal_overrides):
    cfg = Config()
    sig = cfg.signal.model_copy(
        update={"type": "holdings", "k_min": 2, "chase_limit_cents": 5, **signal_overrides}
    )
    return cfg.model_copy(update={"signal": sig})


def make_data(markets, fills, selections=None, price=0.60):
    tapes = {}
    for m in markets:
        tapes[m.yes_token] = flat_tape(T0 - 24 * H, (m.resolved_ts or m.end_ts), price)
        tapes[m.no_token] = flat_tape(T0 - 24 * H, (m.resolved_ts or m.end_ts), 1 - price)
    return SimData(
        markets={m.condition_id: m for m in markets},
        tapes=tapes,
        fills=fills,
        selections=selections or {T0: sorted(fills)},
        wallet_median_cost={},
    )


def run(cfg, data, days=15):
    return Engine(cfg, data, t_start=T0, t_end=T0 + days * 24 * H).run()
