"""Backtest execution model (SPEC §7.7).

Polymarket publishes no historical order books, so fills are approximated from
the trade tape (prices_5m aggregates per token):

buy at t_exec:
  base  = taker-buy VWAP over [t_exec, t_exec + window) if its volume >= $200,
          else last trade before-or-at the window end + 1.5c
  price = base + 1c penalty + slippage, slippage = 0.5c per
          (order_notional / trailing-1h-volume * 10), capped by the chase limit
  if no trades at all within `unfillable_after` minutes of t_exec -> unfillable
sells mirror with taker-sell prices and subtract the adjustments.

All timestamps are epoch seconds; prices in dollars.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TokenTape:
    """Per-token 5-minute bars, ascending bucket_ts (epoch s)."""

    bucket_ts: np.ndarray  # int64
    taker_buy_vwap: np.ndarray  # float, nan where no taker buys
    taker_sell_vwap: np.ndarray
    taker_buy_usdc: np.ndarray
    volume_usdc: np.ndarray
    last_price: np.ndarray

    def last_trade_at(self, ts: int) -> float | None:
        """Last traded price at or before ts."""
        i = int(np.searchsorted(self.bucket_ts, ts, side="right")) - 1
        return float(self.last_price[i]) if i >= 0 else None

    def window(self, t0: int, t1: int) -> slice:
        lo = int(np.searchsorted(self.bucket_ts, t0, side="left"))
        hi = int(np.searchsorted(self.bucket_ts, t1, side="left"))
        return slice(lo, hi)

    def trailing_volume(self, ts: int, hours: float = 1.0) -> float:
        s = self.window(int(ts - hours * 3600), ts)
        return float(np.nansum(self.volume_usdc[s]))

    def has_trade_within(self, ts: int, minutes: int) -> bool:
        s = self.window(ts, ts + minutes * 60)
        return s.stop > s.start

    @property
    def has_volume_data(self) -> bool:
        """False for price-only tapes (Holdout B API prices, d-012): the
        participation/slippage model needs real taker volume."""
        return bool(self.volume_usdc.size and float(np.nanmax(self.volume_usdc)) > 0)


@dataclass
class FillResult:
    price: float
    fill_quality: str  # 'vwap' | 'last_plus' | 'unfillable'
    slippage: float
    base_price: float


def simulate_buy(
    tape: TokenTape,
    *,
    t_exec: int,
    notional_usd: float,
    window_minutes: int = 10,
    penalty: float = 0.01,
    thin_bump: float = 0.015,
    min_window_usdc: float = 200.0,
    unfillable_after_minutes: int = 60,
    price_cap: float | None = None,
) -> FillResult | None:
    """Price for a taker buy of `notional_usd` at t_exec. None = unfillable."""
    if not tape.has_trade_within(t_exec, unfillable_after_minutes):
        return None
    s = tape.window(t_exec, t_exec + window_minutes * 60)
    tb_usdc = float(np.nansum(tape.taker_buy_usdc[s]))
    if tb_usdc >= min_window_usdc:
        w = tape.taker_buy_usdc[s]
        v = tape.taker_buy_vwap[s]
        ok = ~np.isnan(v)
        base = float(np.sum(v[ok] * w[ok]) / np.sum(w[ok]))
        quality = "vwap"
    else:
        # thin window: last trade at-or-before window end, bumped
        last = tape.last_trade_at(t_exec + window_minutes * 60)
        if last is None:
            last = tape.last_trade_at(t_exec + unfillable_after_minutes * 60)
        if last is None:
            return None
        base = last + thin_bump
        quality = "last_plus"
    if tape.has_volume_data:
        trail = tape.trailing_volume(t_exec, 1.0)
        participation = notional_usd / trail if trail > 0 else 10.0
        slippage = 0.005 * participation * 10.0
    else:  # price-only tape: thin_bump+penalty stand in for the slippage model
        slippage = 0.0
    price = base + penalty + slippage
    if price_cap is not None:
        price = min(price, price_cap)
    price = float(np.clip(price, 0.001, 0.999))
    return FillResult(price=price, fill_quality=quality, slippage=slippage, base_price=base)


def simulate_sell(
    tape: TokenTape,
    *,
    t_exec: int,
    notional_usd: float,
    window_minutes: int = 10,
    penalty: float = 0.01,
    thin_bump: float = 0.015,
    min_window_usdc: float = 200.0,
    unfillable_after_minutes: int = 60,
) -> FillResult | None:
    """Mirror of simulate_buy using taker-sell prices, adjustments subtracted."""
    if not tape.has_trade_within(t_exec, unfillable_after_minutes):
        return None
    s = tape.window(t_exec, t_exec + window_minutes * 60)
    ts_usdc = float(np.nansum(tape.volume_usdc[s] - tape.taker_buy_usdc[s]))
    v = tape.taker_sell_vwap[s]
    ok = ~np.isnan(v)
    if ts_usdc >= min_window_usdc and ok.any():
        w = (tape.volume_usdc[s] - tape.taker_buy_usdc[s])[ok]
        base = float(np.sum(v[ok] * w) / np.sum(w)) if np.sum(w) > 0 else float(np.nanmean(v))
        quality = "vwap"
    else:
        last = tape.last_trade_at(t_exec + window_minutes * 60)
        if last is None:
            last = tape.last_trade_at(t_exec + unfillable_after_minutes * 60)
        if last is None:
            return None
        base = last - thin_bump
        quality = "last_plus"
    if tape.has_volume_data:
        trail = tape.trailing_volume(t_exec, 1.0)
        participation = notional_usd / trail if trail > 0 else 10.0
        slippage = 0.005 * participation * 10.0
    else:
        slippage = 0.0
    price = float(np.clip(base - penalty - slippage, 0.001, 0.999))
    return FillResult(price=price, fill_quality=quality, slippage=slippage, base_price=base)
