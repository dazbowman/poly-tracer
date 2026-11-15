"""Build data/markets.parquet from Gamma (open + closed markets).

Usage: uv run python scripts/build_catalog.py [--end-date-min 2023-09-01] [--max-pages N]

The crawl is keyset-paged and restartable only from scratch; pages are cached
on disk for 7 days so a re-run after a crash is cheap.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from polycopy import paths
from polycopy.catalog.markets import normalize_market, rows_to_frame, write_catalog
from polycopy.venues.pm_global.gamma import GammaClient

log = logging.getLogger("build_catalog")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-date-min", default="2023-09-01")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--out", default=str(paths.MARKETS_PARQUET))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    gamma = GammaClient(cache_dir=paths.CACHE / "gamma")

    frames = []
    rows: list[dict] = []
    n_markets = n_skipped = 0
    t0 = time.time()
    for closed in (True, False):  # endpoint defaults to closed=false; crawl both
        for page in gamma.markets_keyset(
            closed=closed,
            end_date_min=args.end_date_min,
            include_tag=True,
            max_pages=args.max_pages,
        ):
            for m in page:
                row = normalize_market(m)
                if row is None:
                    n_skipped += 1
                else:
                    rows.append(row)
                    n_markets += 1
            if len(rows) >= 20_000:
                frames.append(rows_to_frame(rows))
                rows = []
                log.info("…%d markets so far (%.0fs)", n_markets, time.time() - t0)
    if rows:
        frames.append(rows_to_frame(rows))
    if not frames:
        log.error("no markets fetched")
        return 1

    df = write_catalog(frames, paths.MARKETS_PARQUET)
    log.info(
        "catalog: %d markets (%d skipped non-2-token) in %.0fs -> %s",
        len(df),
        n_skipped,
        time.time() - t0,
        args.out,
    )
    print(df.group_by("category_bucket").len().sort("len", descending=True))
    print(df.group_by("status").len().sort("len", descending=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
