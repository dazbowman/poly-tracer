"""Build data/chain_condition_map.parquet from the on-chain condition table.

positionIds order matches Gamma clobTokenIds (verified 2026-11-15): index 0 is
the canonical outcome. payout_yes = numerators[0]/denominator when resolved.
Covers every condition through the dataset end (2026-04-24); Gamma covers the
rest and the metadata.
"""

from __future__ import annotations

import logging
import sys

from polycopy import paths
from polycopy.history import universe

log = logging.getLogger("build_chain_map")

SQL = f"""
SELECT lower(id) AS condition_id,
       positionIds[1] AS yes_token_id,
       positionIds[2] AS no_token_id,
       CASE
         WHEN TRY_CAST(payoutDenominator AS HUGEINT) > 0
         THEN CAST(TRY_CAST(payoutNumerators[1] AS HUGEINT) AS DOUBLE)
              / CAST(TRY_CAST(payoutDenominator AS HUGEINT) AS DOUBLE)
         ELSE NULL
       END AS payout_yes
FROM '{paths.HF_BASE}/condition.parquet'
WHERE len(positionIds) = 2
"""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    con = universe.connect()
    out = paths.DATA / "chain_condition_map.parquet"
    df = con.sql(SQL).pl()
    df.write_parquet(out, compression="zstd")
    resolved = df["payout_yes"].is_not_null().sum()
    log.info("chain map: %d conditions (%d resolved) -> %s", len(df), resolved, out)
    bad = df.filter(df["payout_yes"].is_not_null() & ~df["payout_yes"].is_in([0.0, 0.5, 1.0]))
    if len(bad):
        log.warning(
            "%d conditions with unusual payout_yes values: %s",
            len(bad),
            bad["payout_yes"].unique().to_list()[:10],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
