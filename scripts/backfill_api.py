"""Holdout-B API backfill (d-012, d-020).

Freezes the candidate pool at the last dataset refresh (2026-04-20 features:
top --pool-size by score, plus every wallet any d-017/d-020 config selected on
any cached refresh date), then crawls /v2/trades for each pool wallet from
--start to now and stores normalized fills (fills_norm schema, source role
'api'). Chunked + checkpointed: rerun resumes after the last finished chunk.

Usage: uv run python scripts/backfill_api.py [--pool-size 3000] [--start 2026-04-18]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime

import polars as pl

from polycopy import paths
from polycopy.config import Config
from polycopy.venues.pm_global.data_api import DataApiClient, normalize_api_trades
from polycopy.wallets.select import select_wallets

log = logging.getLogger("backfill")

HB = paths.DATA / "holdout_b"
POOL = HB / "pool.parquet"
TRADES_DIR = HB / "api_fills"
CKPT = HB / "backfill_ckpt.json"
CHUNK = 100  # wallets per output file


def freeze_pool(pool_size: int) -> pl.DataFrame:
    if POOL.exists():
        return pl.read_parquet(POOL)
    feats = pl.read_parquet(paths.DATA / "features" / "asof=2026-04-20.parquet")
    top = (
        feats.sort("score", descending=True, nulls_last=True)
        .head(pool_size)
        .select("wallet")
        .with_columns(src=pl.lit("top_score"))
    )
    # every wallet any shortlisted/finalist config selected on any refresh date
    base = Config.load(paths.REPO / "configs" / "default.yaml")
    sel_wallets: set[str] = set()
    variants: list[tuple[str, int, float]] = [
        ("skill", 40, 0.0),  # V1
        ("skill", 20, 0.0),  # V2
        ("skill", 40, 50.0),  # r2-mmc50 (final)
    ]
    for f in sorted((paths.DATA / "features").glob("asof=*.parquet")):
        fe = pl.read_parquet(f)
        for method, n, mmc in variants:
            cfg = base.model_copy(
                update={
                    "selection": base.selection.model_copy(
                        update={"method": method, "n_wallets": n, "min_median_market_cost": mmc}
                    )
                }
            )
            sel_wallets.update(select_wallets(fe, cfg, method=method, n_wallets=n)["wallet"])
        naive_f = f.with_name(f.name.replace("asof=", "naive="))
        if naive_f.exists():  # V3: naive n10 holdings-era selection
            cfg = base.model_copy(
                update={
                    "selection": base.selection.model_copy(
                        update={"method": "naive_pnl", "n_wallets": 10}
                    )
                }
            )
            sel_wallets.update(
                select_wallets(
                    fe, cfg, method="naive_pnl", n_wallets=10,
                    naive_pnl=pl.read_parquet(naive_f),
                )["wallet"]
            )  # fmt: skip
    sel = pl.DataFrame({"wallet": sorted(sel_wallets)}).with_columns(src=pl.lit("ever_selected"))
    pool = pl.concat([top, sel]).unique(subset="wallet", keep="first").sort("src", "wallet")
    HB.mkdir(parents=True, exist_ok=True)
    pool.write_parquet(POOL)
    log.info("pool frozen: %d wallets (%d ever-selected)", pool.height, sel.height)
    return pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-size", type=int, default=3000)
    ap.add_argument("--start", default="2026-04-18")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    pool = freeze_pool(args.pool_size)
    wallets = pool["wallet"].to_list()
    start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    end_ts = int(time.time())

    token_map = (
        pl.scan_parquet(paths.DATA / "chain_condition_map.parquet")
        .select("yes_token_id", "no_token_id")
        .collect()
    )
    tm = pl.concat(
        [
            token_map.select(token_id=pl.col("yes_token_id")).with_columns(is_yes=pl.lit(True)),
            token_map.select(token_id=pl.col("no_token_id")).with_columns(is_yes=pl.lit(False)),
        ]
    )

    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    done = json.loads(CKPT.read_text())["done_chunks"] if CKPT.exists() else []
    api = DataApiClient(cache_dir=paths.CACHE / "data_api")
    t0 = time.time()
    n_chunks = (len(wallets) + CHUNK - 1) // CHUNK
    for ci in range(n_chunks):
        if ci in done:
            continue
        rows: list[dict] = []
        for w in wallets[ci * CHUNK : (ci + 1) * CHUNK]:
            for page in api.trades(w, start=start_ts, end=end_ts):
                rows.extend(page)
        df = normalize_api_trades(rows, tm)
        if df.height:
            df.write_parquet(TRADES_DIR / f"chunk_{ci:03d}.parquet")
        done.append(ci)
        CKPT.write_text(json.dumps({"done_chunks": done}))
        log.info(
            "chunk %d/%d: %d trades (%.0fs elapsed)", ci + 1, n_chunks, df.height, time.time() - t0
        )
    log.info("backfill complete: %d chunks", n_chunks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
