"""Sign-randomization skill test, shrunk-LCB score, and BH FDR (SPEC §7.2).

Null: a wallet has no directional skill, so flipping the canonical direction of
each market's position flips the sign of that market's edge r_m. The statistic
is the cost-weighted mean edge m = sum(c_i r_i) / sum(c_i).

Under random signs s_i, m_s = sum(c_i s_i r_i) / sum(c_i) has mean 0 and
variance sum((c_i r_i)^2) / (sum c_i)^2 — the normal approximation for n > 100;
smaller wallets get 10,000 actual sign flips.

Score = shrink the whole interval toward 0 with prior strength n0 markets:
score = kappa * (m - 1.64 * se), kappa = n / (n + n0)   (DECISIONS d-005).
"""

from __future__ import annotations

import math

import numpy as np

NORMAL_APPROX_MIN_N = 100


def weighted_mean_edge(costs: np.ndarray, returns: np.ndarray) -> tuple[float, float]:
    """(m, se): cost-weighted mean edge and its null-std (randomization SE)."""
    total = costs.sum()
    if total <= 0:
        return 0.0, float("inf")
    x = costs * returns
    m = x.sum() / total
    se = math.sqrt(np.square(x).sum()) / total
    return float(m), float(se)


def sign_flip_pvalue(
    costs: np.ndarray,
    returns: np.ndarray,
    *,
    n_flips: int = 10_000,
    rng: np.random.Generator | None = None,
) -> float:
    """One-sided p-value of the observed weighted mean edge under sign flips."""
    n = len(returns)
    if n == 0:
        return 1.0
    m, se = weighted_mean_edge(costs, returns)
    if se == 0:
        return 1.0 if m <= 0 else 0.0
    if n > NORMAL_APPROX_MIN_N:
        return float(0.5 * math.erfc(m / (se * math.sqrt(2))))
    rng = rng or np.random.default_rng(1729)
    x = costs * returns
    signs = rng.choice((-1.0, 1.0), size=(n_flips, n))
    sims = signs @ x / costs.sum()
    # permutation p-value with +1 correction (never exactly 0)
    return float((np.count_nonzero(sims >= m) + 1) / (n_flips + 1))


def shrunk_lcb_score(
    costs: np.ndarray, returns: np.ndarray, *, n0: int = 30, z: float = 1.64
) -> float:
    m, se = weighted_mean_edge(costs, returns)
    n = len(returns)
    if n == 0 or not math.isfinite(se):
        return float("-inf")
    kappa = n / (n + n0)
    return float(kappa * (m - z * se))


def benjamini_hochberg(pvalues: np.ndarray, q: float = 0.10) -> np.ndarray:
    """Boolean mask of rejected nulls (skilled) at FDR q."""
    n = len(pvalues)
    if n == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(pvalues)
    ranked = pvalues[order]
    thresh = q * (np.arange(1, n + 1) / n)
    passing = np.nonzero(ranked <= thresh)[0]
    out = np.zeros(n, dtype=bool)
    if passing.size:
        out[order[: passing.max() + 1]] = True
    return out
