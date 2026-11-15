"""Build data/markets.parquet from Gamma (open + closed markets).

Usage: uv run python scripts/build_catalog.py [--end-date-min 2023-09-01] [--max-pages N]

Checkpointed: every ~50k markets a part parquet is written and the keyset
cursor saved, so a killed crawl resumes within one checkpoint. Run it detached
(nohup), not under a session-limited runner.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import polars as pl

from polycopy import paths
from polycopy.catalog.markets import normalize_market, rows_to_frame, write_catalog
from polycopy.venues.pm_global.gamma import GammaClient

log = logging.getLogger("build_catalog")

PARTS_DIR = paths.DATA / "catalog_parts"
CKPT = PARTS_DIR / "checkpoint.json"
PART_ROWS = 50_000


def load_ckpt() -> dict:
    if CKPT.exists():
        return json.loads(CKPT.read_text())
    return {"done_closed_states": [], "cursor": None, "part": 0, "closed": None}


def save_ckpt(state: dict) -> None:
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CKPT.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(CKPT)


def crawl_state(
    gamma: GammaClient, state: dict, closed: bool, end_date_min: str, max_pages: int | None
) -> None:
    """Crawl one closed-state, checkpointing parts; resumes from state['cursor']."""
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    n = 0
    t0 = time.time()
    state["closed"] = closed
    cursor = state.get("cursor")
    for page, next_cursor in gamma.markets_keyset_resumable(
        closed=closed, end_date_min=end_date_min, after_cursor=cursor, max_pages=max_pages
    ):
        for m in page:
            row = normalize_market(m)
            if row is not None:
                rows.append(row)
                n += 1
        if len(rows) >= PART_ROWS:
            part = PARTS_DIR / f"part_{closed}_{state['part']:04d}.parquet"
            rows_to_frame(rows).write_parquet(part, compression="zstd")
            rows = []
            state["part"] += 1
            state["cursor"] = next_cursor
            save_ckpt(state)
            log.info(
                "closed=%s: %d markets, checkpoint %s (%.0fs)",
                closed,
                n,
                part.name,
                time.time() - t0,
            )
    if rows:
        part = PARTS_DIR / f"part_{closed}_{state['part']:04d}.parquet"
        rows_to_frame(rows).write_parquet(part, compression="zstd")
        state["part"] += 1
    state["done_closed_states"].append(closed)
    state["cursor"] = None
    save_ckpt(state)
    log.info("closed=%s finished: %d markets this run", closed, n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-date-min", default="2023-09-01")
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    gamma = GammaClient(cache_dir=paths.CACHE / "gamma")

    state = load_ckpt()
    for closed in (True, False):
        if closed in state["done_closed_states"]:
            log.info("closed=%s already done", closed)
            continue
        if state.get("closed") not in (None, closed):
            state["cursor"] = None  # cursor belongs to the other state
        crawl_state(gamma, state, closed, args.end_date_min, args.max_pages)

    frames = [pl.read_parquet(p) for p in sorted(PARTS_DIR.glob("part_*.parquet"))]
    if not frames:
        log.error("no parts crawled")
        return 1
    df = write_catalog(frames, paths.MARKETS_PARQUET)
    log.info("catalog: %d unique markets -> %s", len(df), paths.MARKETS_PARQUET)
    print(df.group_by("category_bucket").len().sort("len", descending=True))
    print(df.group_by("status").len().sort("len", descending=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
