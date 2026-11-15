"""Phase 2.4 replication check: split each wallet's resolved markets into two
halves (by event-hash parity) and measure how many wallets skilled in half A
are also skilled in half B. Gómez-Cram et al. found ~44% out-of-sample
persistence; we compare order of magnitude, not the exact number.

Usage: uv run python scripts/replication_check.py [--asof 2026-02-02]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

import numpy as np
import polars as pl

from polycopy import paths
from polycopy.config import Config
from polycopy.wallets.skill import benjamini_hochberg, sign_flip_pvalue

log = logging.getLogger("replication")


def half_skill(rets: pl.LazyFrame, cfg: Config, min_markets: int = 15) -> pl.DataFrame:
    grouped = (
        rets.group_by("wallet")
        .agg(
            n=pl.col("condition_id").n_unique(),
            costs=pl.col("buys_usdc").sort_by(pl.col("condition_id")),
            rs=pl.col("r").sort_by(pl.col("condition_id")),
        )
        .filter(pl.col("n") >= min_markets)
        .collect(engine="streaming")
    )
    pvals = []
    for wallet, costs, rs in zip(grouped["wallet"], grouped["costs"], grouped["rs"], strict=True):
        seed = int.from_bytes(wallet[-8:].encode(), "little") % (2**31)
        rng = np.random.default_rng([cfg.seed, seed])
        pvals.append(sign_flip_pvalue(costs.to_numpy(), rs.to_numpy(), rng=rng))
    out = grouped.select("wallet", "n").with_columns(p=pl.Series(pvals, dtype=pl.Float64))
    mask = benjamini_hochberg(out["p"].to_numpy(), q=cfg.selection.fdr_q)
    return out.with_columns(skilled=pl.Series(mask))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-02-02")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(paths.REPO / "configs" / "default.yaml")
    asof = int(datetime.strptime(args.asof, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())

    # precomputed market returns (build_features.py); rows are as-of independent
    rets = (
        pl.scan_parquet(paths.DATA / "market_returns" / "bucket_*.parquet")
        .filter(pl.col("resolved_s") < asof)
        .select("wallet", "condition_id", "buys_usdc", "r", "event_id")
        .with_columns(half=pl.col("event_id").hash(seed=7) % 2)
    )
    log.info("market returns: %d (wallet, market) rows", rets.select(pl.len()).collect().item())

    a = half_skill(rets.filter(pl.col("half") == 0), cfg)
    b = half_skill(rets.filter(pl.col("half") == 1), cfg)
    j = a.join(b, on="wallet", suffix="_b")
    skilled_a = j.filter(pl.col("skilled"))
    persist = skilled_a["skilled_b"].mean() if skilled_a.height else float("nan")
    base_rate = j["skilled_b"].mean()
    print(f"wallets testable in both halves: {j.height}")
    print(f"skilled in A: {skilled_a.height} ({100 * skilled_a.height / max(j.height, 1):.1f}%)")
    print(f"P(skilled in B | skilled in A): {persist:.1%}  (paper: ~44%)")
    print(f"P(skilled in B) base rate:      {base_rate:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
