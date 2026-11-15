import numpy as np
import pytest

from polycopy.sim.fills_backtest import TokenTape, simulate_buy, simulate_sell


def _tape(ts, tb_vwap, ts_vwap, tb_usdc, vol, last):
    return TokenTape(
        bucket_ts=np.array(ts, dtype=np.int64),
        taker_buy_vwap=np.array(tb_vwap, dtype=float),
        taker_sell_vwap=np.array(ts_vwap, dtype=float),
        taker_buy_usdc=np.array(tb_usdc, dtype=float),
        volume_usdc=np.array(vol, dtype=float),
        last_price=np.array(last, dtype=float),
    )


BASE_T = 1_700_000_000


def test_liquid_buy_uses_taker_buy_vwap_plus_penalty_and_slippage():
    # two bars inside the 10m window: 300 and 700 USDC of taker buys at 0.50/0.52
    tape = _tape(
        ts=[BASE_T, BASE_T + 300],
        tb_vwap=[0.50, 0.52],
        ts_vwap=[0.49, 0.51],
        tb_usdc=[300, 700],
        vol=[600, 1400],
        last=[0.50, 0.52],
    )
    # trailing volume over the hour before t_exec is 0 here -> participation huge,
    # so give t_exec a trailing hour with the same bars by moving exec after them?
    # No: t_exec = BASE_T means window [T, T+600) includes both bars; trailing is 0.
    r = simulate_buy(tape, t_exec=BASE_T, notional_usd=100.0, price_cap=None)
    base = (0.50 * 300 + 0.52 * 700) / 1000
    assert r.fill_quality == "vwap"
    assert r.base_price == pytest.approx(base)
    # trailing volume 0 -> participation capped at 10 -> slippage 0.5c*100 = 0.5
    # price clipped to <= 0.999
    assert r.price <= 0.999


def test_slippage_scales_with_participation():
    # 2000 USDC trailing volume in the hour before exec
    tape = _tape(
        ts=[BASE_T - 1800, BASE_T, BASE_T + 60],
        tb_vwap=[0.40, 0.40, 0.40],
        ts_vwap=[0.39, 0.39, 0.39],
        tb_usdc=[2000, 500, 500],
        vol=[2000, 1000, 1000],
        last=[0.40, 0.40, 0.40],
    )
    r = simulate_buy(tape, t_exec=BASE_T, notional_usd=100.0)
    # participation = 100/2000 = 0.05 -> slippage = 0.005*0.05*10 = 0.0025
    assert r.slippage == pytest.approx(0.0025)
    assert r.price == pytest.approx(0.40 + 0.01 + 0.0025)


def test_thin_window_uses_last_plus_bump():
    tape = _tape(
        ts=[BASE_T - 3600, BASE_T + 120],
        tb_vwap=[0.30, 0.31],
        ts_vwap=[0.29, 0.30],
        tb_usdc=[50, 100],  # window taker buys only $100 < $200
        vol=[100, 150],
        last=[0.30, 0.31],
    )
    r = simulate_buy(tape, t_exec=BASE_T, notional_usd=60.0)
    assert r.fill_quality == "last_plus"
    assert r.base_price == pytest.approx(0.31 + 0.015)


def test_unfillable_when_no_trades_within_60m():
    tape = _tape(
        ts=[BASE_T - 7200, BASE_T + 4000],  # nothing within [T, T+3600)
        tb_vwap=[0.5, 0.5],
        ts_vwap=[0.5, 0.5],
        tb_usdc=[500, 500],
        vol=[500, 500],
        last=[0.5, 0.5],
    )
    assert simulate_buy(tape, t_exec=BASE_T, notional_usd=100.0) is None


def test_chase_cap_applies():
    tape = _tape(
        ts=[BASE_T],
        tb_vwap=[0.60],
        ts_vwap=[0.59],
        tb_usdc=[1000],
        vol=[1000],
        last=[0.60],
    )
    r = simulate_buy(tape, t_exec=BASE_T, notional_usd=100.0, price_cap=0.58)
    assert r.price == 0.58


def test_sell_mirrors_down():
    tape = _tape(
        ts=[BASE_T - 1800, BASE_T],
        tb_vwap=[0.50, 0.50],
        ts_vwap=[0.48, 0.48],
        tb_usdc=[500, 500],
        vol=[2500, 1000],  # taker sells = 2000 + 500
        last=[0.50, 0.49],
    )
    r = simulate_sell(tape, t_exec=BASE_T, notional_usd=100.0)
    assert r.fill_quality == "vwap"
    assert r.price < 0.48  # base minus penalty minus slippage
