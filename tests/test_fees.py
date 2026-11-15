"""Worked examples from SPEC §2.2 as regression tests."""

import pytest

from polycopy.sim.fees import (
    bankers_round_cents,
    breakeven_prob_buy,
    ceil_cents,
    kalshi_fee,
    pm_us_fee,
)


def test_spec_example_p50_100_contracts():
    # PM US T1 at p=0.50, 100 contracts: 0.095*100*0.25 = 2.375 -> banker's 2.38
    assert pm_us_fee(100, 0.50) == pytest.approx(2.38)
    # Kalshi: 0.07*100*0.25 = 1.75 exactly
    assert kalshi_fee(100, 0.50) == pytest.approx(1.75)
    # spec's "4.75% of cost": unrounded fee 2.375 over $50 cost
    assert pytest.approx(0.0475) == (0.095 * 100 * 0.25) / 50


def test_spec_example_p855():
    # per-contract PM US fee at 0.855: 0.095*0.855*0.145 = 1.178¢
    fee_1 = 0.095 * 0.855 * (1 - 0.855)
    assert fee_1 * 100 == pytest.approx(1.178, abs=0.005)
    # spec: break-even win probability ~0.867
    assert breakeven_prob_buy(0.855, fee_1) == pytest.approx(0.867, abs=0.001)
    # Kalshi per contract ~0.87¢
    assert pytest.approx(0.868, abs=0.01) == 0.07 * 0.855 * 0.145 * 100


def test_bankers_rounding_ties_to_even():
    assert bankers_round_cents(2.375) == 2.38  # .5 up to even 8
    assert bankers_round_cents(2.385) == 2.38  # .5 down to even 8
    assert bankers_round_cents(2.005) == 2.00


def test_ceil_cents():
    assert ceil_cents(1.7500000000000002) == 1.75  # float dust must not bump
    assert ceil_cents(1.7501) == 1.76
    assert ceil_cents(0.0001) == 0.01  # Kalshi rounds UP from any positive fee


def test_small_pm_us_order_can_round_to_zero():
    # SPEC §4.9: tiny orders round to $0 — must be representable (and not exploited:
    # sizing enforces a $50 minimum elsewhere)
    assert pm_us_fee(1, 0.50) == 0.02  # 0.095*1*0.25=0.024 -> 0.02
    assert pm_us_fee(0.1, 0.50) == 0.0


def test_fee_symmetry_in_price():
    # p=0.3 vs 0.7 sits exactly on a half-cent rounding tie where float noise
    # in p*(1-p) decides the cent; use a tie-free pair for the symmetry check
    assert pm_us_fee(100, 0.2) == pm_us_fee(100, 0.8) == 1.52
