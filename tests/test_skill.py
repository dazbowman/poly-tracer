import math

import numpy as np
import pytest

from polycopy.wallets.skill import (
    benjamini_hochberg,
    shrunk_lcb_score,
    sign_flip_pvalue,
    weighted_mean_edge,
)


def test_weighted_mean_edge_hand_computed():
    costs = np.array([100.0, 300.0])
    rets = np.array([0.10, -0.02])
    m, se = weighted_mean_edge(costs, rets)
    assert m == pytest.approx((100 * 0.10 + 300 * -0.02) / 400)
    assert se == pytest.approx(math.sqrt(10.0**2 + 6.0**2) / 400)


def test_pvalue_small_n_skilled_wallet():
    rng = np.random.default_rng(7)
    # 40 markets, consistently positive edge
    costs = np.full(40, 100.0)
    rets = rng.normal(0.08, 0.05, 40)
    p = sign_flip_pvalue(costs, rets)
    assert p < 0.01


def test_pvalue_small_n_lucky_one_hit():
    # one huge winner among noise: sign test should NOT be fooled badly
    costs = np.array([100.0] * 30 + [10_000.0])
    rets = np.array([0.0] * 30 + [1.0])
    p = sign_flip_pvalue(costs, rets)
    # flipping the single dominant market flips the mean: p should be ~0.5
    assert 0.3 < p < 0.7


def test_pvalue_normal_approx_matches_flips():
    rng = np.random.default_rng(11)
    costs = rng.uniform(50, 500, 150)
    rets = rng.normal(0.03, 0.10, 150)
    p_norm = sign_flip_pvalue(costs, rets)  # n>100 -> normal path
    m, se = weighted_mean_edge(costs, rets)
    assert p_norm == pytest.approx(0.5 * math.erfc(m / (se * math.sqrt(2))), rel=1e-12)
    # against brute flips
    x = costs * rets
    signs = rng.choice((-1.0, 1.0), size=(40_000, 150))
    sims = signs @ x / costs.sum()
    p_emp = (sims >= m).mean()
    assert p_norm == pytest.approx(p_emp, abs=0.01)


def test_zero_edge_pvalue_is_half():
    costs = np.full(200, 100.0)
    rets = np.zeros(200)
    assert sign_flip_pvalue(costs, rets) == pytest.approx(1.0)  # m=0, se=0 -> no evidence


def test_score_shrinks_small_samples():
    costs_small = np.full(10, 100.0)
    costs_big = np.full(200, 100.0)
    rets_small = np.full(10, 0.10)
    rets_big = np.full(200, 0.10)
    # identical per-market edge, dispersionless... add tiny noise for nonzero se
    rng = np.random.default_rng(3)
    rets_small = rets_small + rng.normal(0, 0.01, 10)
    rets_big = rets_big + rng.normal(0, 0.01, 200)
    assert shrunk_lcb_score(costs_small, rets_small) < shrunk_lcb_score(costs_big, rets_big)


def test_bh_rejects_obvious_and_controls_nulls():
    p = np.array([0.0001, 0.0002, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.95, 0.85])
    mask = benjamini_hochberg(p, q=0.10)
    assert mask[0] and mask[1]
    assert mask.sum() == 2


def test_bh_empty():
    assert benjamini_hochberg(np.array([])).size == 0
