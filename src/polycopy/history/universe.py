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
  -- ts slice on the RAW string: equal-length digit strings compare
  -- lexicographically like numbers, so parquet zone maps can prune
  WHERE "timestamp" >= ? AND "timestamp" < ?
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


def out_path_for(remote_file: str, slice_idx: int = 0) -> Path:
    # .../order_filled/year=2024/month=03.parquet -> 2024_month=03_s0.parquet
    parts = remote_file.split("/")
    year = parts[-2].split("=")[1]
    stem = parts[-1].removesuffix(".parquet")
    return paths.UNIVERSE_DIR / "wallet_month" / f"{year}_{stem}_s{slice_idx}.parquet"


def ts_slices(remote_file: str, n_slices: int) -> list[tuple[str, str]]:
    """Equal time slices of the file's month as raw 10-digit-string bounds."""
    import itertools
    from datetime import UTC, datetime

    parts = remote_file.split("/")
    year = int(parts[-2].split("=")[1])
    month = int(parts[-1].removesuffix(".parquet").split("=")[1])
    t0 = int(datetime(year, month, 1, tzinfo=UTC).timestamp())
    t1 = int(datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC).timestamp())
    # interior edges aligned to 300s so every 5-minute price bucket falls
    # wholly inside one slice (slice outputs then just concatenate)
    edges = [(t0 + (t1 - t0) * i // n_slices) // 300 * 300 for i in range(n_slices)] + [t1]
    # widen the outermost bounds: chain ts can straggle outside the calendar month
    bounds = [str(e) for e in edges]
    bounds[0] = "0000000000"
    bounds[-1] = "9999999999"
    return list(itertools.pairwise(bounds))


def process_file(con: duckdb.DuckDBPyConnection, remote_file: str, n_slices: int = 1) -> None:
    todo = [
        (i, lo, hi)
        for i, (lo, hi) in enumerate(ts_slices(remote_file, n_slices))
        if not out_path_for(remote_file, i).exists()
    ]
    if not todo:
        log.info("skip (exists): %s", remote_file.split("/")[-1])
        return
    no_cash = con.sql(NO_CASH_LEG_SQL, params=[remote_file]).fetchall()[0][0]
    if no_cash:
        log.warning("%s: %d fills with no USDC leg (excluded from volume)", remote_file, no_cash)
    for i, lo, hi in todo:
        out = out_path_for(remote_file, i)
        out.parent.mkdir(parents=True, exist_ok=True)
        df = con.sql(WALLET_MONTH_SQL, params=[remote_file, lo, hi]).pl()
        tmp = out.with_suffix(".tmp.parquet")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(out)
        log.info(
            "%s slice %d/%d -> %d wallet-months",
            remote_file.split("/")[-1],
            i + 1,
            n_slices,
            len(df),
        )


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


def derive_extract_set(
    wallet_month: pl.DataFrame,
    *,
    min_volume_usd: float = 25_000,
    min_tokens: int = 30,
    min_fills: int = 30,
    max_fills_per_day: float = 200.0,
    max_fills_total: int = 100_000,
) -> pl.DataFrame:
    """Pass-2 extraction wallets (DECISIONS d-013): necessary conditions of the
    §7.1 gates plus cadence caps that drop hyperactive bot/MM accounts."""
    tot = wallet_month.group_by("wallet").agg(
        vol=pl.col("volume_usdc").sum(),
        fills=pl.col("n_fills").sum(),
        tok=pl.col("n_tokens").sum(),
        first=pl.col("first_ts").min(),
        last=pl.col("last_ts").max(),
    )
    return (
        tot.with_columns(days=(pl.col("last") - pl.col("first")) / 86_400 + 1)
        .with_columns(fpd=pl.col("fills") / pl.col("days"))
        .filter(
            (pl.col("vol") >= min_volume_usd)
            & (pl.col("tok") >= min_tokens)
            & (pl.col("fills") >= min_fills)
            & (pl.col("fpd") <= max_fills_per_day)
            & (pl.col("fills") <= max_fills_total)
        )
        .sort("vol", descending=True)
    )
