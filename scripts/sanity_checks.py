"""Phase 1 sanity checks (SPEC §9 Phase 1.6). Prints a report block for
reports/phase-1.md.

1. Token->market mapping coverage: fills_norm rows (mapped by construction)
   vs the extract wallets' total legs from pass-1 aggregates.
2. Monthly volume plausibility: prices_5m totals vs public figures.
3. 20 random fills cross-checked against Data API /v2/trades.
"""

from __future__ import annotations

import logging
import sys
import time

import polars as pl

from polycopy import paths
from polycopy.util.http import PoliteClient

log = logging.getLogger("sanity")


def check_mapping_coverage() -> None:
    legs = pl.scan_parquet(paths.FILLS_DIR / "*.parquet").select(pl.len()).collect().item()
    wm = pl.read_parquet(paths.UNIVERSE_DIR / "wallet_month.parquet")
    ex = pl.read_parquet(paths.UNIVERSE_DIR / "candidates_extract.parquet").select("wallet")
    expected = (
        wm.join(ex, on="wallet", how="inner").filter(pl.col("month") <= "2026-04")["n_fills"].sum()
    )
    print(
        f"[1] mapping coverage: fills_norm rows={legs:,} expected legs={expected:,} "
        f"-> {100 * legs / expected:.2f}% (SPEC wants >=99%)"
    )


def check_monthly_volumes() -> None:
    v = (
        pl.scan_parquet(paths.PRICES_DIR / "*.parquet")
        .with_columns(month=pl.from_epoch("bucket_ts").dt.strftime("%Y-%m"))
        .group_by("month")
        .agg(vol_usd=pl.col("volume_usdc").sum())
        .sort("month")
        .collect()
    )
    print("[2] monthly matched volume (USD, both-leg counted once):")
    for r in v.iter_rows(named=True):
        print(f"    {r['month']}: ${r['vol_usd'] / 1e9:.2f}B")
    print(
        "    compare: public reporting put Polymarket at roughly $2.5B in 2024-11 "
        "(US election), ~$1B/mo mid-2025, and all-time-high months in 2026 "
        "(sports + 5-min crypto). Order of magnitude must agree."
    )


def check_api_crosscheck(n: int = 20) -> None:
    # sample from two month files only (memory-light), takers (trade events)
    some_files = sorted((paths.FILLS_DIR).glob("*.parquet"))[-20::7]
    fills = (
        pl.concat([pl.read_parquet(f) for f in some_files[:3]])
        .filter(pl.col("role") == "taker")
        .sample(n=400, seed=7)
    )
    api = PoliteClient("https://data-api.polymarket.com", cache_dir=paths.CACHE / "data_api")
    matched = tried = 0
    for row in fills.iter_rows(named=True):
        if tried >= n:
            break
        tried += 1
        body = api.get(
            "/v2/trades",
            {
                "user": row["wallet"],
                "start": row["ts"] - 1,
                "end": row["ts"] + 2,
                "limit": 100,
            },
            cache_ttl=7 * 86400,
        )
        for tr in body.get("data", []):
            if (
                tr["token_id"] == row["token_id"]
                and abs(tr["size"] - row["shares"]) < 0.01 + 0.001 * row["shares"]
                and tr["timestamp"] == row["ts"]
            ):
                matched += 1
                break
        time.sleep(0.05)
    print(f"[3] API cross-check: {matched}/{tried} sampled taker fills found in /v2/trades")
    if matched < tried * 0.9:
        print("    WARNING: below 90% — investigate before trusting fills_norm")


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    check_mapping_coverage()
    check_monthly_volumes()
    check_api_crosscheck()
    return 0


if __name__ == "__main__":
    sys.exit(main())
