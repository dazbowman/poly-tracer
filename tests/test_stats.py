import numpy as np
import polars as pl
import pytest

from polycopy.backtest.stats import brier_score, event_bootstrap_roi, max_drawdown


def _trades(rows):
    return pl.DataFrame(
        rows, schema={"event_id": pl.Utf8, "net_pnl": pl.Float64, "cost": pl.Float64}, orient="row"
    )


def test_roi_point_estimate():
    t = _trades([("e1", 10.0, 100.0), ("e1", -5.0, 100.0), ("e2", 20.0, 200.0)])
    r = event_bootstrap_roi(t, n_resamples=2000)
    assert r.roi == pytest.approx(25 / 400)
    assert r.n_events == 2 and r.n_trades == 3


def test_clearly_positive_strategy_has_small_p():
    rows = [(f"e{i}", 10.0, 100.0) for i in range(60)]
    r = event_bootstrap_roi(_trades(rows), n_resamples=4000)
    assert r.p_one_sided < 0.01
    assert r.ci_lo > 0


def test_noise_strategy_has_large_p():
    rng = np.random.default_rng(2)
    rows = [(f"e{i}", float(rng.normal(0, 10)), 100.0) for i in range(60)]
    r = event_bootstrap_roi(_trades(rows), n_resamples=4000)
    assert r.p_one_sided > 0.05 or abs(r.roi) < 0.02  # no false confidence


def test_bootstrap_keeps_events_together():
    # one whale event drives everything; CI must be wide, p must be weak
    rows = [(f"e{i}", 0.0, 100.0) for i in range(30)] + [("whale", 500.0, 100.0)]
    r = event_bootstrap_roi(_trades(rows), n_resamples=4000)
    # resamples that exclude the whale have roi ~0 -> p(ROI<=0) far from tiny
    assert r.p_one_sided > 0.02
    assert r.ci_lo <= 0.0 < r.ci_hi


def test_determinism_by_seed():
    rows = [(f"e{i}", float(i % 5 - 2), 50.0) for i in range(40)]
    a = event_bootstrap_roi(_trades(rows), seed=7)
    b = event_bootstrap_roi(_trades(rows), seed=7)
    assert (a.ci_lo, a.ci_hi, a.p_one_sided) == (b.ci_lo, b.ci_hi, b.p_one_sided)


def test_max_drawdown():
    eq = np.array([100.0, 120.0, 90.0, 95.0, 130.0, 110.0])
    assert max_drawdown(eq) == pytest.approx(30 / 120)
    assert max_drawdown(np.array([1.0, 2.0, 3.0])) == 0.0


def test_brier():
    assert brier_score(np.array([0.8, 0.3]), np.array([1.0, 0.0])) == pytest.approx(
        ((0.2) ** 2 + 0.09) / 2
    )
