import polars as pl
import pytest

from polycopy.wallets.pnl import market_returns

MK = pl.DataFrame(
    {
        "condition_id": ["c1", "c2", "c3"],
        "event_id": ["e1", "e2", "e3"],
        "category_bucket": ["politics", "sports", "crypto"],
        "payout_yes": [1.0, 0.0, None],  # c3 unresolved
        "resolved_ts": [1_000_000 * 1000, 2_000_000 * 1000, None],
    }
).lazy()


def _fill(wallet, cond, ts, canon_dir, bought, shares, usdc, role="taker"):
    return {
        "wallet": wallet,
        "condition_id": cond,
        "ts": ts,
        "canon_dir": canon_dir,
        "bought": bought,
        "shares": shares,
        "usdc": usdc,
        "role": role,
    }


def test_win_on_yes_and_no_side_loss():
    fills = pl.DataFrame(
        [
            # w1 buys 100 Yes of c1 @0.60, holds; c1 resolves YES -> pnl = 100 - 60
            _fill("w1", "c1", 10, 1, True, 100.0, 60.0),
            # w1 buys 100 No of c2 @0.30 (canon short); c2 resolves NO -> No pays 1
            _fill("w1", "c2", 20, -1, True, 100.0, 30.0),
            # w2 buys 50 Yes of c2 @0.65, sells 20 @0.50, holds 30; c2 resolves NO
            _fill("w2", "c2", 30, 1, True, 50.0, 32.5),
            _fill("w2", "c2", 40, -1, False, 20.0, 10.0),
        ]
    ).lazy()
    out = market_returns(fills, MK).collect().sort("wallet", "condition_id")
    w1c1 = out.filter((pl.col("wallet") == "w1") & (pl.col("condition_id") == "c1")).row(
        0, named=True
    )
    assert w1c1["pnl_usdc"] == pytest.approx(40.0)
    assert w1c1["r"] == pytest.approx(40 / 60)
    w1c2 = out.filter((pl.col("wallet") == "w1") & (pl.col("condition_id") == "c2")).row(
        0, named=True
    )
    # No side: terminal_no=100, payout_yes=0 -> value 100; pnl = 100 - 30
    assert w1c2["pnl_usdc"] == pytest.approx(70.0)
    w2c2 = out.filter(pl.col("wallet") == "w2").row(0, named=True)
    # sells 10 + terminal_yes 30 * payout 0 - buys 32.5
    assert w2c2["pnl_usdc"] == pytest.approx(10 - 32.5)
    assert w2c2["r"] == pytest.approx(-22.5 / 32.5)


def test_unresolved_market_excluded():
    fills = pl.DataFrame([_fill("w1", "c3", 10, 1, True, 10.0, 5.0)]).lazy()
    assert market_returns(fills, MK).collect().height == 0


def test_asof_excludes_late_resolutions_and_fills():
    fills = pl.DataFrame(
        [
            _fill("w1", "c1", 10, 1, True, 100.0, 60.0),
            _fill("w1", "c2", 20, 1, True, 100.0, 50.0),
        ]
    ).lazy()
    # asof between c1 (1.0M s) and c2 (2.0M s) resolutions
    out = market_returns(fills, MK, asof_ts=1_500_000).collect()
    assert out["condition_id"].to_list() == ["c1"]


def test_sell_only_market_excluded_from_returns():
    # cost = 0 -> no r; must not divide by zero (split-sourced inventory)
    fills = pl.DataFrame([_fill("w1", "c1", 10, 1, False, 10.0, 6.0)]).lazy()
    assert market_returns(fills, MK).collect().height == 0
