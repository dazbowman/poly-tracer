"""Venue fee formulas (SPEC §2.2, Appendix A). Money math — keep exact.

All fees are taker fees per ORDER (not per contract): coefficient * C * p * (1-p),
where C is the contract count and p the execution price. Rounding differs by
venue and is applied once per order:
- Polymarket US: banker's rounding to the cent (round-half-even)
- Kalshi: ceiling to the cent
- Polymarket global: live per-token rate; no documented rounding (left unrounded)
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal

PM_US_THETA_T1 = 0.095
KALSHI_COEFF = 0.07


def bankers_round_cents(x: float) -> float:
    """Round to nearest cent, ties to even (Polymarket US documented rounding)."""
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def ceil_cents(x: float) -> float:
    # guard float dust (e.g. 1.7500000000000002 must not ceil to 1.76)
    return math.ceil(round(x, 9) * 100) / 100


def pm_us_fee(contracts: float, price: float, theta: float = PM_US_THETA_T1) -> float:
    return bankers_round_cents(theta * contracts * price * (1.0 - price))


def kalshi_fee(contracts: float, price: float, coeff: float = KALSHI_COEFF) -> float:
    return ceil_cents(coeff * contracts * price * (1.0 - price))


def pm_global_fee(contracts: float, price: float, rate: float) -> float:
    """rate from CLOB /fee-rate per token (decoded to a fraction by the caller)."""
    return contracts * rate * price * (1.0 - price)


def breakeven_prob_buy(price: float, fee_per_contract: float) -> float:
    """Minimum true probability for a taker buy held to resolution to break even.

    Solve q*1 + (1-q)*0 - price - fee = 0  ->  q = price + fee_per_contract.
    """
    return price + fee_per_contract
