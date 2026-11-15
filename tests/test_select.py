import polars as pl
import pytest

from polycopy.config import Config
from polycopy.wallets.select import select_wallets, window_marked_pnl

CFG = Config()


def _features(rows):
    return pl.DataFrame(
        rows,
        schema={
            "asof_ts": pl.Int64,
            "wallet": pl.Utf8,
            "label": pl.Utf8,
            "skilled": pl.Boolean,
            "score": pl.Float64,
        },
        orient="row",
    )


def test_skill_selection_excludes_filtered_and_unskilled():
    feats = _features(
        [
            (1, "a", "skilled_directional", True, 0.08),
            (1, "b", "skilled_directional", True, 0.12),
            (1, "mm", "market_maker", True, 0.50),  # filtered despite score
            (1, "c", "unskilled", False, 0.30),  # not FDR-passing
            (1, "d", "skilled_directional", True, -0.01),  # negative score
        ]
    )
    sel = select_wallets(feats, CFG, method="skill", n_wallets=3)
    assert sel["wallet"].to_list() == ["b", "a"]
    assert sel["rank"].to_list() == [1, 2]


def test_naive_selection_ranks_by_window_pnl_with_filters():
    feats = _features(
        [
            (1, "a", "unskilled", False, 0.0),
            (1, "b", "skilled_directional", True, 0.1),
            (1, "bot", "bot_arb", False, 0.0),
        ]
    )
    naive = pl.DataFrame({"wallet": ["a", "b", "bot"], "naive_window_pnl": [900.0, 500.0, 99e9]})
    sel = select_wallets(feats, CFG, method="naive_pnl", n_wallets=2, naive_pnl=naive)
    assert sel["wallet"].to_list() == ["a", "b"]  # bot excluded, else by pnl


def test_window_marked_pnl_flows_and_marks():
    # wallet buys 100 sh @0.40 before t0; sells 50 @0.70 inside window; mark t1=0.80
    fills = pl.DataFrame(
        {
            "wallet": ["w", "w"],
            "token_id": ["tk", "tk"],
            "ts": [50, 150],
            "bought": [True, False],
            "shares": [100.0, 50.0],
            "usdc": [40.0, 35.0],
        }
    ).lazy()
    prices = pl.DataFrame(
        {
            "token_id": ["tk", "tk", "tk"],
            "bucket_ts": [50, 150, 190],
            "last_price": [0.40, 0.70, 0.80],
        }
    ).lazy()
    out = window_marked_pnl(fills, prices, t0=100, t1=200)
    # pos0=100 @ mark(t0)=0.40 -> 40; pos1=50 @ 0.80 -> 40; cash +35
    assert out.row(0, named=True)["naive_window_pnl"] == pytest.approx(35 + 40 - 40)


def test_window_pnl_counts_only_window_flows():
    # buy before window is not a flow; equity change comes from marks
    fills = pl.DataFrame(
        {
            "wallet": ["w"],
            "token_id": ["tk"],
            "ts": [50],
            "bought": [True],
            "shares": [100.0],
            "usdc": [40.0],
        }
    ).lazy()
    prices = pl.DataFrame(
        {"token_id": ["tk", "tk"], "bucket_ts": [60, 180], "last_price": [0.40, 0.90]}
    ).lazy()
    out = window_marked_pnl(fills, prices, t0=100, t1=200)
    assert out.row(0, named=True)["naive_window_pnl"] == pytest.approx(100 * 0.90 - 100 * 0.40)
