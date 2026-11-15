"""Extract non-fill position events (split/merge/redemption/neg-risk conversion)
and the proxy->signer wallet map for candidate wallets.

Usage: uv run python scripts/build_wallet_events.py
Outputs: data/wallet_events.parquet, data/wallet_map.parquet
Requires: data/universe/candidates.parquet.
"""

from __future__ import annotations

import logging
import sys

from polycopy import paths
from polycopy.history import universe

log = logging.getLogger("build_wallet_events")

EVENTS_SQL = """
WITH cand AS (SELECT wallet FROM read_parquet(?)),
ev AS (
  SELECT 'split' AS kind, lower(stakeholder) AS wallet, lower(condition) AS condition_id,
         TRY_CAST("timestamp" AS BIGINT) AS ts,
         CAST(TRY_CAST(amount AS HUGEINT) AS DOUBLE) / 1e6 AS amount_usdc,
         NULL AS extra
  FROM read_parquet(?)
  UNION ALL
  SELECT 'merge', lower(stakeholder), lower(condition),
         TRY_CAST("timestamp" AS BIGINT),
         CAST(TRY_CAST(amount AS HUGEINT) AS DOUBLE) / 1e6, NULL
  FROM read_parquet(?)
  UNION ALL
  SELECT 'redeem', lower(redeemer), lower(condition),
         TRY_CAST("timestamp" AS BIGINT),
         CAST(TRY_CAST(payout AS HUGEINT) AS DOUBLE) / 1e6,
         array_to_string(indexSets, ',')
  FROM read_parquet(?)
  UNION ALL
  SELECT 'convert', lower(stakeholder), lower(negRiskMarketId),
         TRY_CAST("timestamp" AS BIGINT),
         CAST(TRY_CAST(amount AS HUGEINT) AS DOUBLE) / 1e6,
         indexSet || '/' || questionCount
  FROM read_parquet(?)
)
SELECT * FROM ev WHERE wallet IN (SELECT wallet FROM cand)
"""

WALLET_MAP_SQL = """
SELECT lower(id) AS wallet, lower(signer) AS signer, type
FROM read_parquet(?)
WHERE lower(id) IN (SELECT wallet FROM read_parquet(?))
   OR lower(signer) IN (SELECT wallet FROM read_parquet(?))
"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    con = universe.connect()
    cand = str(paths.UNIVERSE_DIR / "candidates.parquet")
    base = paths.HF_BASE
    years = "year=202[456]"

    out = paths.DATA / "wallet_events.parquet"
    rel = con.sql(
        EVENTS_SQL,
        params=[
            cand,
            f"{base}/split/{years}/*.parquet",
            f"{base}/merge/{years}/*.parquet",
            f"{base}/redemption/{years}/*.parquet",
            f"{base}/neg_risk_conversion/{years}/*.parquet",
        ],
    )
    rel.write_parquet(str(out), compression="zstd")
    n = con.sql("SELECT count(*), count(DISTINCT wallet) FROM read_parquet(?)", params=[str(out)])
    log.info("wallet_events: %s rows / wallets -> %s", n.fetchall()[0], out)

    outw = paths.WALLET_MAP_PARQUET
    con.sql(WALLET_MAP_SQL, params=[f"{base}/wallet.parquet", cand, cand]).write_parquet(
        str(outw), compression="zstd"
    )
    nw = con.sql("SELECT count(*) FROM read_parquet(?)", params=[str(outw)]).fetchall()[0][0]
    log.info("wallet_map: %d rows -> %s", nw, outw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
