"""Universe pass 1: wallet-month aggregates + candidate wallets.

Usage: uv run python scripts/build_universe.py [--years 2024 2025 2026] [--files-limit N]
Resumable: per-file outputs under data/universe/wallet_month/ are skipped if present.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from polycopy import paths
from polycopy.history import universe

log = logging.getLogger("build_universe")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025, 2026])
    ap.add_argument("--files-limit", type=int, default=None, help="process at most N files")
    ap.add_argument("--skip-aggregate", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    con = universe.connect()
    files = universe.list_fill_files(con, args.years)
    log.info("%d remote files to process", len(files))
    if args.files_limit:
        files = files[: args.files_limit]
    t0 = time.time()
    for i, f in enumerate(files, 1):
        # 2026 month files are ~14.5 GB and the grouped-agg input spill does not
        # fit the disk allowance in one shot; slice big months by timestamp
        n_slices = 8 if "year=2026" in f else 1
        universe.process_file(con, f, n_slices=n_slices)
        log.info("[%d/%d] %.0fs elapsed", i, len(files), time.time() - t0)

    if args.skip_aggregate:
        return 0
    wm = universe.combine_wallet_month()
    wm.write_parquet(paths.UNIVERSE_DIR / "wallet_month.parquet", compression="zstd")
    cands = universe.derive_candidates(wm)
    cands.write_parquet(paths.UNIVERSE_DIR / "candidates.parquet", compression="zstd")
    log.info(
        "wallet-months: %d | wallets: %d | candidates: %d | candidate volume share: %.1f%%",
        len(wm),
        wm["wallet"].n_unique(),
        len(cands),
        100 * cands["total_volume_usdc"].sum() / wm["volume_usdc"].sum(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
