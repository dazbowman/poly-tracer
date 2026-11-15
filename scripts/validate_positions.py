"""Phase 2.2 validation: replay fills+events through WalletBook and compare
realized PnL with the chain subgraph's user_position.realizedPnl.

Sample: 50 extract wallets whose first fill is >= 2024-01-01 (our fills start
there), mixed split/merge intensity. Target (SPEC): median abs error < 5% for
wallets with split_merge share < 0.1. Realization semantics mirror the
subgraph: sells and redemptions realize; open positions do NOT auto-settle.

Usage: uv run python scripts/validate_positions.py [--n 50]
"""

from __future__ import annotations

import argparse
import logging
import sys

import polars as pl

from polycopy import paths
from polycopy.history import universe
from polycopy.wallets.positions import WalletBook

log = logging.getLogger("validate")


def sample_wallets(n: int) -> pl.DataFrame:
    wm = pl.read_parquet(paths.UNIVERSE_DIR / "wallet_month.parquet")
    ex = pl.read_parquet(paths.UNIVERSE_DIR / "candidates_extract.parquet")
    ev = pl.read_parquet(paths.DATA / "wallet_events.parquet")
    ev_w = ev.group_by("wallet").agg(ev_usdc=pl.col("amount_usdc").sum())
    t2024 = 1_704_067_200  # 2024-01-01
    pool = (
        ex.join(wm.group_by("wallet").agg(first=pl.col("first_ts").min()), on="wallet")
        .filter(pl.col("first") >= t2024)
        .join(ev_w, on="wallet", how="left")
        .with_columns(pl.col("ev_usdc").fill_null(0.0))
        .with_columns(sm_share=pl.col("ev_usdc") / (pl.col("ev_usdc") + pl.col("vol")))
    )
    clean = pool.filter(pl.col("sm_share") < 0.02).sample(n=n // 2, seed=42)
    mixed = pool.filter(pl.col("sm_share") >= 0.02).sample(
        n=min(n - n // 2, pool.filter(pl.col("sm_share") >= 0.02).height), seed=42
    )
    return pl.concat([clean, mixed]).select("wallet", "sm_share", "vol")


def replay_wallet(
    wallet: str, fills: pl.DataFrame, events: pl.DataFrame, payouts: dict[str, float]
) -> float:
    book = WalletBook()
    f = fills.select(
        "ts",
        "condition_id",
        kind=pl.lit("fill"),
        is_yes=(pl.col("canon_dir") == 1) == pl.col("bought"),
        bought=pl.col("bought"),
        shares=pl.col("shares"),
        usdc=pl.col("usdc"),
    )
    e = events.select(
        "ts",
        "condition_id",
        kind=pl.col("kind"),
        is_yes=pl.lit(True),
        bought=pl.lit(True),
        shares=pl.col("amount_usdc"),  # split/merge amounts are share-pairs
        usdc=pl.col("amount_usdc"),
    )
    merged = pl.concat([f, e]).sort("ts")
    for r in merged.iter_rows(named=True):
        cond = r["condition_id"]
        if r["kind"] == "fill":
            book.apply_fill(
                cond, is_yes=r["is_yes"], bought=r["bought"], shares=r["shares"], usdc=r["usdc"]
            )
        elif r["kind"] == "split":
            book.condition(cond).apply_split(r["shares"])
        elif r["kind"] == "merge":
            book.condition(cond).apply_merge(r["shares"])
        elif r["kind"] == "redeem":
            payout = payouts.get(cond)
            if payout is not None:
                book.condition(cond).apply_redeem(payout)
        # 'convert' (neg-risk) not modeled; wallets using it are flagged anyway
    return book.total_realized_pnl()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sample = sample_wallets(args.n)
    wallets = sample["wallet"].to_list()
    log.info("sampled %d wallets", len(wallets))

    fills = (
        pl.scan_parquet(paths.FILLS_DIR / "*.parquet")
        .filter(pl.col("wallet").is_in(wallets))
        .collect()
    )
    events = (
        pl.scan_parquet(paths.DATA / "wallet_events.parquet")
        .filter(pl.col("wallet").is_in(wallets))
        .collect()
    )
    chain = pl.read_parquet(paths.DATA / "chain_condition_map.parquet")
    payouts = {
        r["condition_id"]: r["payout_yes"]
        for r in chain.filter(pl.col("payout_yes").is_not_null()).iter_rows(named=True)
    }

    con = universe.connect()
    log.info("querying user_position for the sample (full remote scan, be patient)…")
    up = con.sql(
        f"""
        SELECT lower(user) AS wallet,
               sum(TRY_CAST(realizedPnl AS DOUBLE)) / 1e6 AS chain_realized
        FROM '{paths.HF_BASE}/user_position.parquet'
        WHERE lower(user) IN (SELECT UNNEST(?))
        GROUP BY 1
        """,
        params=[wallets],
    ).pl()

    rows = []
    for w in wallets:
        mine = replay_wallet(
            w,
            fills.filter(pl.col("wallet") == w),
            events.filter(pl.col("wallet") == w),
            payouts,
        )
        rows.append({"wallet": w, "replay_realized": mine})
    res = (
        pl.DataFrame(rows)
        .join(up, on="wallet", how="left")
        .join(sample, on="wallet")
        .with_columns(
            abs_err=(pl.col("replay_realized") - pl.col("chain_realized")).abs(),
        )
        .with_columns(
            rel_err=pl.col("abs_err")
            / pl.max_horizontal(pl.col("chain_realized").abs(), pl.lit(100.0))
        )
        .sort("rel_err")
    )
    out = paths.DATA / "validation_positions.parquet"
    res.write_parquet(out)
    clean = res.filter(pl.col("sm_share") < 0.1)
    print(res.select("wallet", "sm_share", "replay_realized", "chain_realized", "rel_err"))
    print(
        f"\nmedian rel err (split/merge < 0.1): {clean['rel_err'].median():.3%} "
        f"(n={clean.height}; SPEC target < 5%)"
    )
    print(f"median rel err (all): {res['rel_err'].median():.3%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
