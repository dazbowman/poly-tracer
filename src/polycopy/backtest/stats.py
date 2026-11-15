"""Uncertainty for backtest results (SPEC §8.3): block bootstrap over EVENTS.

Trades within one event share an outcome, so the resample unit is the event:
draw events with replacement, keep each drawn event's trades together, and
recompute ROI = sum(net pnl) / sum(cost). One-sided p-value for ROI > 0 is the
bootstrap fraction of resamples at or below zero (with +1 smoothing).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass
class BootstrapResult:
    roi: float
    ci_lo: float
    ci_hi: float
    p_one_sided: float  # P(ROI <= 0) under the bootstrap distribution
    n_trades: int
    n_events: int


def event_bootstrap_roi(
    trades: pl.DataFrame,
    *,
    pnl_col: str = "net_pnl",
    cost_col: str = "cost",
    event_col: str = "event_id",
    n_resamples: int = 10_000,
    seed: int = 1729,
    ci: float = 0.95,
) -> BootstrapResult:
    """trades: one row per resolved trade with net pnl, cost, event id."""
    per_event = (
        trades.group_by(event_col)
        .agg(pnl=pl.col(pnl_col).sum(), cost=pl.col(cost_col).sum())
        .sort(event_col)
    )
    pnl = per_event["pnl"].to_numpy()
    cost = per_event["cost"].to_numpy()
    n_ev = len(pnl)
    total_cost = cost.sum()
    if n_ev == 0 or total_cost <= 0:
        return BootstrapResult(0.0, 0.0, 0.0, 1.0, 0, 0)
    roi = float(pnl.sum() / total_cost)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_ev, size=(n_resamples, n_ev))
    boot_pnl = pnl[idx].sum(axis=1)
    boot_cost = cost[idx].sum(axis=1)
    ok = boot_cost > 0
    rois = boot_pnl[ok] / boot_cost[ok]
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(rois, [alpha, 1 - alpha])
    p = float((np.count_nonzero(rois <= 0) + 1) / (len(rois) + 1))
    return BootstrapResult(
        roi=roi,
        ci_lo=float(lo),
        ci_hi=float(hi),
        p_one_sided=p,
        n_trades=trades.height,
        n_events=n_ev,
    )


def max_drawdown(equity: np.ndarray) -> float:
    """Max peak-to-trough drawdown as a fraction of the peak."""
    if len(equity) == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    dd = (peaks - equity) / peaks
    return float(dd.max())


def brier_score(prices: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean squared error of entry prices (as probabilities) vs outcomes."""
    if len(prices) == 0:
        return float("nan")
    return float(np.mean((prices - outcomes) ** 2))
