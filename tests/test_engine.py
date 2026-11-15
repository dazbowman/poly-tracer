"""Synthetic-world tests of the backtest engine: consensus, guards, episodes,
latency execution, resolution settlement, determinism."""

import numpy as np
import pytest

from polycopy.backtest.engine import Engine, MarketMeta, SimData, WalletFills
from polycopy.config import Config
from polycopy.sim.fees import pm_us_fee
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


def test_holdings_consensus_entry_and_resolution_pnl():
    m = market(payout=1.0)
    # both wallets buy 600 USDC of Yes at 0.60 shortly after T0
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.60, 600),
    }
    cfg = base_cfg()
    res = run(cfg, make_data([m], fills))
    assert len(res.signals) == 1
    sig = res.signals[0]
    assert sig["canon_dir"] == 1 and sig["n_wallets"] == 2
    buys = [t for t in res.trades if t["side"] == "buy" and t["status"] == "open"]
    assert len(buys) == 1
    tr = buys[0]
    # latency: exec 30 min after the signal tick
    assert tr["ts_exec"] - sig["ts"] == cfg.execution.latency_minutes * 60
    # price = vwap 0.60 + 1c penalty + slippage; fee per PM US formula
    assert tr["avg_price"] == pytest.approx(0.61, abs=0.02)
    assert tr["fee"] == pytest.approx(pm_us_fee(tr["shares"], tr["avg_price"]))
    resolved = [t for t in res.trades if t["side"] == "resolve"]
    assert len(resolved) == 1 and resolved[0]["avg_price"] == 1.0
    expected_pnl = tr["shares"] * 1.0 - tr["notional"] - tr["fee"]
    assert res.final_equity == pytest.approx(cfg.bankroll_usd + expected_pnl, abs=0.01)


def test_one_wallet_is_not_consensus():
    m = market()
    fills = {"w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600)}
    res = run(base_cfg(), make_data([m], fills))
    assert res.signals == []


def test_short_consensus_buys_no_token():
    m = market(payout=0.0)  # resolves NO
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.40, 600, yes=False),
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.40, 600, yes=False),
    }
    res = run(base_cfg(), make_data([m], fills))
    assert len(res.signals) == 1
    assert res.signals[0]["canon_dir"] == -1
    tr = next(t for t in res.trades if t["side"] == "buy")
    # engine buys the NO token around its 0.40 tape price
    assert tr["avg_price"] == pytest.approx(0.41, abs=0.02)
    resolved = next(t for t in res.trades if t["side"] == "resolve")
    assert resolved["avg_price"] == 1.0  # NO pays out


def test_conflicting_wallets_net_below_k():
    m = market()
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
        "w2": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
        # dissenter lands in the same tick, so the first evaluation nets 1 < k
        "w3": wallet_buys("c1", [T0 + 2 * H], 0.40, 600, yes=False),
    }
    res = run(base_cfg(), make_data([m], fills))
    assert res.signals == []  # net = 2 - 1 = 1 < k


def test_chase_guard_blocks_when_price_ran():
    m = market()
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.50, 600),  # entered at 0.50
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.50, 600),
    }
    # market trades at 0.60 -> 0.60 > 0.50 + 5c cap
    res = run(base_cfg(), make_data([m], fills, price=0.60))
    assert res.signals == []
    assert all(t["side"] != "buy" for t in res.trades)


def test_price_band_blocks_extremes():
    m = market()
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.95, 600),
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.95, 600),
    }
    res = run(base_cfg(), make_data([m], fills, price=0.95))  # above price_max 0.92
    assert res.signals == []


def test_one_entry_per_episode_and_event_dedupe():
    m = market()
    # wallets keep adding -> consensus stays >= k; only one entry allowed
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H, T0 + 30 * H, T0 + 60 * H], 0.60, 600),
        "w2": wallet_buys("c1", [T0 + 3 * H, T0 + 31 * H, T0 + 61 * H], 0.60, 600),
    }
    res = run(base_cfg(), make_data([m], fills))
    assert len([t for t in res.trades if t["side"] == "buy"]) == 1


def test_fresh_flow_requires_recent_adds():
    m = market(end_h=24 * 40, resolved_h=24 * 41)
    cfg = base_cfg(type="fresh_flow", flow_window_hours=24, min_wallet_add_usd=250)
    # adds happened on day 1; by day 20 nothing fresh -> exactly one signal early
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.60, 600),
    }
    res = run(cfg, make_data([m], fills), days=30)
    assert len(res.signals) == 1
    assert res.signals[0]["ts"] <= T0 + 4 * H


def test_unfillable_counted_when_tape_dies():
    m = market()
    data = make_data(
        [m],
        {
            "w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
            "w2": wallet_buys("c1", [T0 + 3 * H], 0.60, 600),
        },
    )
    # kill the yes tape after the signal: no trades within 60m of exec
    yt = data.tapes["c1-y"]
    cut = np.searchsorted(yt.bucket_ts, T0 + 3 * H)
    data.tapes["c1-y"] = TokenTape(
        bucket_ts=yt.bucket_ts[:cut],
        taker_buy_vwap=yt.taker_buy_vwap[:cut],
        taker_sell_vwap=yt.taker_sell_vwap[:cut],
        taker_buy_usdc=yt.taker_buy_usdc[:cut],
        volume_usdc=yt.volume_usdc[:cut],
        last_price=yt.last_price[:cut],
    )
    res = run(base_cfg(), data)
    assert res.unfillable == 1
    assert all(t["status"] != "open" for t in res.trades)


def test_selection_refresh_drops_wallet():
    m = market()
    # w2 only selected from week 2; both wallets' fills exist from day 1
    sels = {T0: ["w1"], T0 + 7 * 24 * H: ["w1", "w2"]}
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.60, 600),
    }
    res = run(base_cfg(), make_data([m], fills, selections=sels))
    # consensus only possible after w2 joins at week 2
    assert len(res.signals) == 1
    assert res.signals[0]["ts"] >= T0 + 7 * 24 * H


def test_determinism():
    m = market()
    fills = {
        "w1": wallet_buys("c1", [T0 + 2 * H], 0.60, 600),
        "w2": wallet_buys("c1", [T0 + 3 * H], 0.60, 600),
    }
    r1 = run(base_cfg(), make_data([m], fills))
    r2 = run(base_cfg(), make_data([m], fills))
    assert r1.decision_log == r2.decision_log
    assert r1.final_equity == r2.final_equity
