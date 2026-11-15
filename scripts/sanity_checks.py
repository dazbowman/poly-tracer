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
    """Direct check on three sample months: candidate owner events on the chain
    vs rows that made it into fills_norm (the token-map join drops unmapped)."""
    import duckdb

    from polycopy.history.fills import _EXCH_SQL

    con = duckdb.connect()
    cand = str(paths.UNIVERSE_DIR / "candidates_extract.parquet")
    for ym, remote in [
        ("2024-10", "year=2024/month=10"),
        ("2025-06", "year=2025/month=06"),
        ("2026-03", "year=2026/month=03"),
    ]:
        expected = con.sql(
            f"""
            SELECT count(*) FROM '{paths.HF_BASE}/order_filled/{remote}.parquet'
            WHERE (makerAssetId='0' OR takerAssetId='0')
              AND lower(maker) NOT IN {_EXCH_SQL}
              AND lower(maker) IN (SELECT wallet FROM read_parquet('{cand}'))
            """
        ).fetchall()[0][0]
        got = (
            pl.scan_parquet(paths.FILLS_DIR / f"fills_{ym}_s*.parquet")
            .select(pl.len())
            .collect()
            .item()
        )
        print(
            f"[1] mapping coverage {ym}: fills_norm={got:,} owner-events={expected:,} "
            f"-> {100 * got / expected:.2f}% (SPEC wants >=99%)"
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
            # API size is NET of the taker fee charged in the received asset
            # (up to ~5% on fee-enabled markets); ours is gross matched
            if (
                tr["token_id"] == row["token_id"]
                and tr["timestamp"] == row["ts"]
                and abs(tr["size"] - row["shares"]) <= 0.01 + 0.06 * row["shares"]
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
