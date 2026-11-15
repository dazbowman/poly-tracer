"""Run one backtest config on a named split.

Usage:
  uv run python scripts/run_backtest.py --config configs/default.yaml --split train
  uv run python scripts/run_backtest.py --config configs/default.yaml --split train \
      --override selection.method=naive_pnl --override selection.n_wallets=10

Splits (SPEC §8.1): train, train_ex_election, validation, holdout_a, holdout_b.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from polycopy import paths
from polycopy.backtest.data import load_sim_data
from polycopy.backtest.experiments import run_experiment
from polycopy.config import Config

SPLITS = {
    "train": ("2024-07-01", "2025-09-30"),
    "validation": ("2025-10-01", "2026-01-31"),
    "holdout_a": ("2026-02-01", "2026-04-24"),
    "smoke": ("2025-01-01", "2025-03-31"),
}


def ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def apply_overrides(cfg: Config, overrides: list[str]) -> Config:
    data: dict[str, Any] = cfg.model_dump()
    for ov in overrides:
        key, _, val = ov.partition("=")
        node = data
        parts = key.split(".")
        for p in parts[:-1]:
            node = node[p]
        leaf = parts[-1]
        old = node[leaf]
        if isinstance(old, bool):
            node[leaf] = val.lower() in ("1", "true", "yes")
        elif val.lower() in ("none", "null"):
            node[leaf] = None
        elif isinstance(old, int) and not isinstance(old, bool):
            node[leaf] = int(val)
        elif isinstance(old, float):
            node[leaf] = float(val)
        else:
            node[leaf] = val
    return Config.model_validate(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--split", default="train", choices=sorted(SPLITS))
    ap.add_argument("--override", action="append", default=[])
    ap.add_argument("--label", default="")
    ap.add_argument("--save-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = apply_overrides(Config.load(args.config), args.override)
    t0, t1 = (ts(d) for d in SPLITS[args.split])
    data = load_sim_data(cfg, t_start=t0, t_end=t1)
    run_dir = paths.DATA / "runs" / cfg.config_hash() if args.save_run else None
    run_experiment(
        cfg, data, split=args.split, t_start=t0, t_end=t1, run_dir=run_dir, label=args.label
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
