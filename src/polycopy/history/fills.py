"""Pass 2 (SPEC §9 Phase 1.4): normalized candidate fills + 5m price bars per token.

Fill semantics (SPEC §3.1): the side whose assetId is '0' pays USDC and BUYS the
other side's outcome token. Amounts are 1e6-scaled integers. The taker is the
aggressor: makerAssetId != '0' (maker provides the token) means the taker BOUGHT.

Canonical direction (SPEC §4.1): +1 = buys Yes-equivalent. Buying No counts as
selling Yes at 1 - p, so canon_dir = +1 iff (BUY and yes-token) or (SELL and
no-token); price_yes_equiv = p for the yes token, 1 - p for the no token.
"""

from __future__ import annotations

import logging

import duckdb

from polycopy import paths

log = logging.getLogger(__name__)

# token map: token_id -> (condition_id, is_yes); candidates: wallet
FILLS_NORM_SQL = """
WITH f AS (
  SELECT lower(maker) AS maker, lower(taker) AS taker,
         makerAssetId AS ma, takerAssetId AS ta,
         TRY_CAST(makerAmountFilled AS HUGEINT) AS mamt,
         TRY_CAST(takerAmountFilled AS HUGEINT) AS tamt,
         TRY_CAST("timestamp" AS BIGINT) AS ts
  FROM read_parquet(?)
  WHERE (makerAssetId = '0' OR takerAssetId = '0')
    AND "timestamp" >= ? AND "timestamp" < ?
),
fills AS (
  SELECT ts, maker, taker,
         CASE WHEN ma = '0' THEN ta ELSE ma END AS token_id,
         CAST(CASE WHEN ma = '0' THEN mamt ELSE tamt END AS DOUBLE) / 1e6 AS usdc,
         CAST(CASE WHEN ma = '0' THEN tamt ELSE mamt END AS DOUBLE) / 1e6 AS shares,
         (ma = '0') AS maker_bought
  FROM f
),
legs AS (
  -- tx hash and counterparty dropped for size (DECISIONS d-014)
  SELECT ts, maker AS wallet, token_id, usdc, shares,
         maker_bought AS bought, 'maker' AS role
  FROM fills
  UNION ALL
  SELECT ts, taker, token_id, usdc, shares,
         NOT maker_bought, 'taker'
  FROM fills
)
SELECT l.ts, l.wallet,
       t.condition_id,
       l.token_id,
       CASE WHEN l.bought = t.is_yes THEN 1 ELSE -1 END AS canon_dir,
       l.shares, l.usdc,
       CASE WHEN t.is_yes THEN l.usdc / l.shares
            ELSE 1.0 - l.usdc / l.shares END AS price_yes_equiv,
       l.bought, l.role
FROM legs l
JOIN token_map t ON l.token_id = t.token_id
WHERE l.wallet IN (SELECT wallet FROM candidates) AND l.shares > 0
"""

PRICES_5M_SQL = """
WITH f AS (
  SELECT makerAssetId AS ma, takerAssetId AS ta,
         TRY_CAST(makerAmountFilled AS HUGEINT) AS mamt,
         TRY_CAST(takerAmountFilled AS HUGEINT) AS tamt,
         TRY_CAST("timestamp" AS BIGINT) AS ts
  FROM read_parquet(?)
  WHERE (makerAssetId = '0' OR takerAssetId = '0')
    AND "timestamp" >= ? AND "timestamp" < ?
),
fills AS (
  SELECT ts,
         CASE WHEN ma = '0' THEN ta ELSE ma END AS token_id,
         CAST(CASE WHEN ma = '0' THEN mamt ELSE tamt END AS DOUBLE) / 1e6 AS usdc,
         CAST(CASE WHEN ma = '0' THEN tamt ELSE mamt END AS DOUBLE) / 1e6 AS shares,
         (ma <> '0') AS taker_bought
  FROM f
)
SELECT token_id,
       (ts // 300) * 300 AS bucket_ts,
       sum(usdc) / sum(shares) AS vwap,
       sum(usdc) FILTER (taker_bought) / nullif(sum(shares) FILTER (taker_bought), 0)
         AS taker_buy_vwap,
       sum(usdc) FILTER (NOT taker_bought) / nullif(sum(shares) FILTER (NOT taker_bought), 0)
         AS taker_sell_vwap,
       sum(usdc) AS volume_usdc,
       sum(usdc) FILTER (taker_bought) AS taker_buy_usdc,
       count(*) AS n_trades,
       arg_max(usdc / shares, ts) AS last_price
FROM fills
WHERE shares > 0
GROUP BY 1, 2
ORDER BY 1, 2
"""


def register_reference_tables(con: duckdb.DuckDBPyConnection) -> None:
    """token_map from the on-chain condition table (complete through 2026-04-24,
    token order verified against Gamma); candidates from pass 1."""
    chain_map = paths.DATA / "chain_condition_map.parquet"
    con.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW token_map AS
        SELECT yes_token_id AS token_id, condition_id, TRUE AS is_yes
        FROM read_parquet('{chain_map}')
        UNION ALL
        SELECT no_token_id, condition_id, FALSE
        FROM read_parquet('{chain_map}')
        """
    )
    con.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW candidates AS
        SELECT wallet FROM read_parquet('{paths.UNIVERSE_DIR / "candidates_extract.parquet"}')
        """
    )


def month_tag(remote_file: str) -> str:
    # .../year=2025/month=03.parquet -> 2025-03
    parts = remote_file.split("/")
    return f"{parts[-2].split('=')[1]}-{parts[-1].split('=')[1].split('.')[0]}"


def process_file(con: duckdb.DuckDBPyConnection, remote_file: str, n_slices: int = 1) -> None:
    """Extract fills_norm + prices_5m for one month, in ts slices (big 2026
    months spill otherwise; interior edges are 300s-aligned so prices buckets
    never straddle slices — see universe.ts_slices)."""
    from polycopy.history.universe import ts_slices

    tag = month_tag(remote_file)
    paths.FILLS_DIR.mkdir(parents=True, exist_ok=True)
    paths.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    for i, (lo, hi) in enumerate(ts_slices(remote_file, n_slices)):
        fills_out = paths.FILLS_DIR / f"fills_{tag}_s{i}.parquet"
        prices_out = paths.PRICES_DIR / f"prices5m_{tag}_s{i}.parquet"
        if not fills_out.exists():
            tmp = fills_out.with_suffix(".tmp.parquet")
            con.sql(FILLS_NORM_SQL, params=[remote_file, lo, hi]).write_parquet(
                str(tmp), compression="zstd"
            )
            tmp.replace(fills_out)
            log.info("%s s%d fills_norm done", tag, i)
        if not prices_out.exists():
            tmp = prices_out.with_suffix(".tmp.parquet")
            con.sql(PRICES_5M_SQL, params=[remote_file, lo, hi]).write_parquet(
                str(tmp), compression="zstd"
            )
            tmp.replace(prices_out)
            log.info("%s s%d prices_5m done", tag, i)
