"""B0 matched-baseline band for a saved run's round trips (SPEC §8.2).

For each resolved strategy trade, sample R random taker-buy opportunities in the
same (category, price±2c, TTR bucket, entry week); replication r buys the r-th
draw of every trade with the trade's notional, +1c penalty and PM-US fees, held
to resolution. The R portfolio ROIs are the baseline band the strategy must beat.

Usage:
  uv run python scripts/run_baseline.py --run-dir data/runs/<hash> --split train \
      [--label s1-skill-n20] [-R 400]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import numpy as np
import polars as pl

from polycopy import paths
from polycopy.backtest.baselines import b0_sample, build_opportunity_pool
from polycopy.backtest.data import load_markets_frame
from run_backtest import SPLITS, ts  # same scripts/ dir

log = logging.getLogger("baseline")

BASELINES = paths.DATA / "baselines.parquet"
HOUR_S = 3600


def hourly_pool(t0: int, t1: int) -> pl.DataFrame:
    """Hourly taker-buy cells inside the window, joined to resolved markets."""
    prices_h = (
        pl.scan_parquet(paths.PRICES_DIR / "*.parquet")
        .filter((pl.col("bucket_ts") >= t0) & (pl.col("bucket_ts") < t1))
        .filter(pl.col("taker_buy_usdc") > 0)
        .group_by("token_id", (pl.col("bucket_ts") // HOUR_S * HOUR_S).alias("bucket_ts"))
        .agg(
            taker_buy_vwap=(
                (pl.col("taker_buy_vwap") * pl.col("taker_buy_usdc")).sum()
                / pl.col("taker_buy_usdc").sum()
            ),
            taker_buy_usdc=pl.col("taker_buy_usdc").sum(),
            volume_usdc=pl.col("volume_usdc").sum(),
        )
    )
    markets = load_markets_frame().lazy()
    return build_opportunity_pool(prices_h, markets).collect(engine="streaming")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--split", required=True, choices=sorted(SPLITS))
    ap.add_argument("--label", default="")
    ap.add_argument("-R", type=int, default=400, help="baseline replications")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0, t1 = (ts(d) for d in SPLITS[args.split])

    rt = pl.read_parquet(paths.REPO / args.run_dir / f"roundtrips_{args.split}.parquet")
    resolved = rt.filter(pl.col("net_pnl").is_not_null() & pl.col("end_s").is_not_null())
    strat_roi = resolved["net_pnl"].sum() / resolved["cost"].sum()
    trades = resolved.select(
        "entry_ts", "entry_price", notional=pl.col("cost"), category="category",
        end_s="end_s", event_id="event_id",
    )  # fmt: skip
    log.info("strategy: %d resolved trades, roi=%.4f", trades.height, strat_roi)

    tp = time.time()
    pool = hourly_pool(t0, t1)
    log.info("opportunity pool: %d cells (%.0fs)", pool.height, time.time() - tp)

    samples = b0_sample(trades, pool, n_per_trade=args.R, seed=1729)
    matched_idx = sorted(samples["strategy_trade_idx"].unique().to_list())
    matched = len(matched_idx)
    log.info("matched %d/%d trades", matched, trades.height)
    # apples to apples: the strategy portfolio restricted to matched trades
    strat_matched = resolved[matched_idx]
    strat_roi_m = strat_matched["net_pnl"].sum() / strat_matched["cost"].sum()

    # replication r = r-th draw of each matched trade -> portfolio ROI
    rois = []
    per_trade = samples.sort("strategy_trade_idx").partition_by("strategy_trade_idx")
    costs = np.array([g["cost"][0] for g in per_trade])
    pnls = [g["net_pnl"].to_numpy() for g in per_trade]
    total_cost = costs.sum()
    for r in range(args.R):
        pnl_r = sum(p[r % len(p)] for p in pnls)
        rois.append(pnl_r / total_cost)
    rois_arr = np.array(rois)
    lo, mid, hi = np.percentile(rois_arr, [2.5, 50, 97.5])
    excess = strat_roi_m - rois_arr.mean()
    p_beat = float((rois_arr >= strat_roi_m).mean())  # share of baseline draws >= strategy

    row = {
        "label": args.label or args.run_dir,
        "split": args.split,
        "n_trades": trades.height,
        "n_matched": matched,
        "strategy_roi": float(strat_roi),
        "strategy_roi_matched": float(strat_roi_m),
        "b0_mean": float(rois_arr.mean()),
        "b0_lo": float(lo),
        "b0_med": float(mid),
        "b0_hi": float(hi),
        "excess_roi": float(excess),
        "p_baseline_geq_strategy": p_beat,
        "R": args.R,
        "ts": int(time.time()),
    }
    df = pl.DataFrame([row])
    if BASELINES.exists():
        df = pl.concat([pl.read_parquet(BASELINES), df], how="diagonal")
    df.write_parquet(BASELINES)
    print(
        f"strategy roi {strat_roi:+.4f} (matched subset {strat_roi_m:+.4f}) | "
        f"B0 mean {rois_arr.mean():+.4f} band ({lo:+.4f}, {hi:+.4f}) | "
        f"excess {excess:+.4f} | P(B0 >= strat) {p_beat:.3f} | "
        f"matched {matched}/{trades.height}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
