"""Hand-computed scenarios per SPEC §9 Phase 2.1: partial sells, No-side buys,
0.5 payouts, split/merge, oversell behavior."""

import pytest

from polycopy.wallets.positions import ConditionPosition, WalletBook


def test_buy_then_partial_sell_then_win():
    c = ConditionPosition()
    c.apply_fill(is_yes=True, bought=True, shares=100, usdc=40)  # 100 @ 0.40
    c.apply_fill(is_yes=True, bought=True, shares=100, usdc=60)  # 100 @ 0.60 -> avg 0.50
    assert c.yes.avg_price == pytest.approx(0.50)
    c.apply_fill(is_yes=True, bought=False, shares=50, usdc=35)  # sell 50 @ 0.70
    assert c.yes.realized_pnl == pytest.approx((0.70 - 0.50) * 50)
    assert c.yes.shares == pytest.approx(150)
    pnl = c.settle(payout_yes=1.0)  # 150 shares pay $1
    assert pnl == pytest.approx((1.0 - 0.50) * 150)
    assert c.realized_pnl == pytest.approx(10 + 75)
    # lifetime identity: cash out - cash in + terminal value
    assert c.realized_pnl == pytest.approx(35 + 150 * 1.0 - 100)


def test_no_side_buy_is_canonical_short():
    c = ConditionPosition()
    c.apply_fill(is_yes=False, bought=True, shares=200, usdc=30)  # 200 No @ 0.15
    assert c.net_yes_shares == pytest.approx(-200)
    assert c.canonical_entry_price == pytest.approx(1 - 0.15)  # short Yes at 0.85
    # Yes resolves 0 -> No pays 1
    pnl = c.settle(payout_yes=0.0)
    assert pnl == pytest.approx((1.0 - 0.15) * 200)


def test_half_payout():
    c = ConditionPosition()
    c.apply_fill(is_yes=True, bought=True, shares=100, usdc=80)  # Yes @ 0.80
    c.apply_fill(is_yes=False, bought=True, shares=40, usdc=8)  # No @ 0.20
    pnl = c.settle(payout_yes=0.5)
    assert pnl == pytest.approx((0.5 - 0.8) * 100 + (0.5 - 0.2) * 40)


def test_split_then_sell_both_sides():
    c = ConditionPosition()
    c.apply_split(100)  # $100 -> 100 Yes + 100 No, imputed 0.50 each
    c.apply_fill(is_yes=True, bought=False, shares=100, usdc=60)  # sell Yes @ 0.60
    c.apply_fill(is_yes=False, bought=False, shares=100, usdc=42)  # sell No @ 0.42
    assert c.realized_pnl == pytest.approx((0.60 - 0.50) * 100 + (0.42 - 0.50) * 100)
    assert c.net_yes_shares == pytest.approx(0)


def test_merge_realizes_against_basis():
    c = ConditionPosition()
    c.apply_fill(is_yes=True, bought=True, shares=100, usdc=55)
    c.apply_fill(is_yes=False, bought=True, shares=100, usdc=42)
    c.apply_merge(100)  # pair -> $1, imputed 0.50 per side
    assert c.realized_pnl == pytest.approx((0.5 - 0.55) * 100 + (0.5 - 0.42) * 100)
    assert c.yes.shares == pytest.approx(0) and c.no.shares == pytest.approx(0)


def test_oversell_goes_negative_not_crash():
    c = ConditionPosition()
    c.apply_fill(is_yes=True, bought=False, shares=50, usdc=30)  # sell with no inventory
    assert c.yes.shares == pytest.approx(-50)
    assert c.net_yes_shares == pytest.approx(-50)


def test_wallet_book_routes_by_condition():
    b = WalletBook()
    b.apply_fill("0xaaa", is_yes=True, bought=True, shares=10, usdc=5)
    b.apply_fill("0xbbb", is_yes=False, bought=True, shares=10, usdc=5)
    assert b.condition("0xaaa").net_yes_shares == 10
    assert b.condition("0xbbb").net_yes_shares == -10
    assert b.total_realized_pnl() == 0


def test_mark_to_market():
    c = ConditionPosition()
    c.apply_fill(is_yes=True, bought=True, shares=100, usdc=40)
    assert c.value_at(0.55) == pytest.approx(55)
    assert c.unrealized_pnl_at(0.55) == pytest.approx(15)
