"""Universe pass 1 (SPEC §9 Phase 1.3): wallet-month aggregates over HF order_filled.

Processed one remote parquet file at a time (resumable; outputs per file), then
candidates are derived from the combined aggregates.

order_filled semantics (SPEC §3.1): every fill has exactly one USDC leg
(assetId '0'); the USDC amount is that fill's notional for both participants.
All columns are VARCHAR in the source — cast before math.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import polars as pl

from polycopy import paths

log = logging.getLogger(__name__)

WALLET_MONTH_SQL = """
WITH f AS (
  SELECT lower(maker) AS m, lower(taker) AS t,
         makerAssetId AS ma, takerAssetId AS ta,
         TRY_CAST(makerAmountFilled AS HUGEINT) AS mamt,
         TRY_CAST(takerAmountFilled AS HUGEINT) AS tamt,
         TRY_CAST("timestamp" AS BIGINT) AS ts
  FROM read_parquet(?)
),
legs AS (
  SELECT m AS wallet, TRUE AS is_maker,
         CASE WHEN ma = '0' THEN mamt ELSE tamt END AS usdc_raw,
         CASE WHEN ma = '0' THEN ta ELSE ma END AS token,
         ts
  FROM f
  UNION ALL
  SELECT t, FALSE,
         CASE WHEN ma = '0' THEN mamt ELSE tamt END,
         CASE WHEN ma = '0' THEN ta ELSE ma END,
         ts
  FROM f
)
SELECT wallet,
       strftime(to_timestamp(ts), '%Y-%m') AS month,
       CAST(sum(usdc_raw) / 1e6 AS DOUBLE) AS volume_usdc,
       CAST(coalesce(sum(usdc_raw) FILTER (is_maker), 0) / 1e6 AS DOUBLE) AS maker_volume_usdc,
       count(*)::BIGINT AS n_fills,
       -- distinct over a 64-bit hash: the 78-char token strings make the exact
       -- distinct-state spill tens of GB on 2026 months (collisions irrelevant
       -- for thresholding)
       count(DISTINCT hash(token))::BIGINT AS n_tokens,
       min(ts) AS first_ts,
       max(ts) AS last_ts
FROM legs
GROUP BY 1, 2
"""

NO_CASH_LEG_SQL = """
SELECT count(*) FROM read_parquet(?)
WHERE makerAssetId <> '0' AND takerAssetId <> '0'
"""


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET memory_limit='10GB'")
    tmp = paths.DATA / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory='{tmp}'")
    return con


def list_fill_files(con: duckdb.DuckDBPyConnection, years: list[int]) -> list[str]:
    files: list[str] = []
    for y in years:
        rows = con.sql(
            "SELECT file FROM glob(?) ORDER BY file",
            params=[f"{paths.HF_BASE}/order_filled/year={y}/*.parquet"],
        ).fetchall()
        files.extend(r[0] for r in rows)
    return files


def out_path_for(remote_file: str) -> Path:
    # .../order_filled/year=2024/part-0003.parquet -> 2024_part-0003.parquet
    parts = remote_file.split("/")
    year = parts[-2].split("=")[1]
    return paths.UNIVERSE_DIR / "wallet_month" / f"{year}_{parts[-1]}"


def process_file(con: duckdb.DuckDBPyConnection, remote_file: str) -> Path:
    out = out_path_for(remote_file)
    if out.exists():
        log.info("skip (exists): %s", out.name)
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    no_cash = con.sql(NO_CASH_LEG_SQL, params=[remote_file]).fetchall()[0][0]
    if no_cash:
        log.warning("%s: %d fills with no USDC leg (excluded from volume)", remote_file, no_cash)
    df = con.sql(WALLET_MONTH_SQL, params=[remote_file]).pl()
    tmp = out.with_suffix(".tmp.parquet")
    df.write_parquet(tmp, compression="zstd")
    tmp.replace(out)
    log.info("%s -> %s (%d wallet-months)", remote_file.split("/")[-1], out.name, len(df))
    return out


def combine_wallet_month() -> pl.DataFrame:
    """Merge per-file aggregates (a wallet-month can span files) into one frame."""
    df = pl.read_parquet(paths.UNIVERSE_DIR / "wallet_month" / "*.parquet")
    return (
        df.group_by("wallet", "month")
        .agg(
            pl.col("volume_usdc").sum(),
            pl.col("maker_volume_usdc").sum(),
            pl.col("n_fills").sum(),
            pl.col("n_tokens").sum(),  # upper bound across files; fine for thresholds
            pl.col("first_ts").min(),
            pl.col("last_ts").max(),
        )
        .sort("wallet", "month")
    )


def derive_candidates(
    wallet_month: pl.DataFrame,
    *,
    min_window_volume_usd: float = 10_000,
    window_months: int = 6,
    min_total_fills: int = 10,
) -> pl.DataFrame:
    """Candidates: >= min volume in any `window_months`-month rolling window."""
    wm = wallet_month.with_columns(
        (
            pl.col("month").str.slice(0, 4).cast(pl.Int32) * 12
            + pl.col("month").str.slice(5, 2).cast(pl.Int32)
        ).alias("month_idx")
    )
    rolled = (
        wm.sort("wallet", "month_idx")
        .rolling(index_column="month_idx", period=f"{window_months}i", group_by="wallet")
        .agg(pl.col("volume_usdc").sum().alias("window_volume"))
        .group_by("wallet")
        .agg(pl.col("window_volume").max())
    )
    totals = wm.group_by("wallet").agg(
        pl.col("volume_usdc").sum().alias("total_volume_usdc"),
        pl.col("maker_volume_usdc").sum().alias("total_maker_volume_usdc"),
        pl.col("n_fills").sum().alias("total_fills"),
        pl.col("first_ts").min(),
        pl.col("last_ts").max(),
        pl.col("month").n_unique().alias("n_months"),
    )
    return (
        totals.join(rolled, on="wallet")
        .filter(
            (pl.col("window_volume") >= min_window_volume_usd)
            & (pl.col("total_fills") >= min_total_fills)
        )
        .sort("total_volume_usdc", descending=True)
    )
