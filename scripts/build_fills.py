"""Pass 2: fills_norm (candidates only) + prices_5m (all tokens), per month.

Usage: uv run python scripts/build_fills.py [--years 2024 2025 2026] [--files-limit N]
Requires: data/markets.parquet (build_catalog) and data/universe/candidates.parquet
(build_universe). Resumable per month.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from polycopy.history import fills, universe

log = logging.getLogger("build_fills")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025, 2026])
    ap.add_argument("--files-limit", type=int, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    con = universe.connect()
    fills.register_reference_tables(con)
    files = universe.list_fill_files(con, args.years)
    if args.files_limit:
        files = files[: args.files_limit]
    t0 = time.time()
    for i, f in enumerate(files, 1):
        fills.process_file(con, f)
        log.info("[%d/%d] %.0fs elapsed", i, len(files), time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
