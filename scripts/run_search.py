"""Staged parameter search on Train (SPEC §8.4; <=300 configs total).

Stage 1: selection method x N with the default signal.
Stage 2: signal family sweep on chosen selections.
Stage 3: exits + guard variants on the stage-2 shortlist.

Every run lands in the experiments registry with its config hash. SimData is
cached per (method, N) because selection defines the watched set; signal/exit
sweeps reuse the same SimData in-process.

Usage:
  uv run python scripts/run_backtest.py ... for one-offs; this script for sweeps:
  uv run python scripts/run_search.py --stage 1 --split train
  uv run python scripts/run_search.py --stage 2 --split train --method skill --n 20 10
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sys

from polycopy import paths
from polycopy.backtest.data import load_sim_data
from polycopy.backtest.experiments import run_experiment
from polycopy.config import Config
from run_backtest import SPLITS, apply_overrides, ts  # same scripts/ dir

log = logging.getLogger("search")


def stage1(cfg0: Config, split: str, t0: int, t1: int) -> None:
    for method, n in itertools.product(["skill", "naive_pnl"], [5, 10, 20, 40]):
        cfg = apply_overrides(cfg0, [f"selection.method={method}", f"selection.n_wallets={n}"])
        data = load_sim_data(cfg, t_start=t0, t_end=t1)
        run_experiment(cfg, data, split=split, t_start=t0, t_end=t1, label=f"s1-{method}-n{n}")


def stage2(cfg0: Config, split: str, t0: int, t1: int, method: str, ns: list[int]) -> None:
    for n in ns:
        base = apply_overrides(cfg0, [f"selection.method={method}", f"selection.n_wallets={n}"])
        data = load_sim_data(base, t_start=t0, t_end=t1)
        grids: list[list[str]] = []
        # holdings family
        for k in (2, 3, 5):
            for conv in (1.0, 2.0):
                grids.append(
                    [
                        "signal.type=holdings",
                        f"signal.k_min={k}",
                        f"signal.min_conviction_ratio={conv}",
                    ]
                )
        # fresh_flow family
        for k in (2, 3):
            for win in (6, 24, 72):
                grids.append(
                    [
                        "signal.type=fresh_flow",
                        f"signal.k_min={k}",
                        f"signal.flow_window_hours={win}",
                    ]
                )
        # single wallet diagnostic
        grids.append(["signal.type=single_wallet", "signal.k_min=1"])
        # chase variants on the defaults
        for chase in (2, 10, None):
            grids.append(
                [
                    "signal.type=fresh_flow",
                    f"signal.chase_limit_cents={'none' if chase is None else chase}",
                ]
            )
        for ovs in grids:
            cfg = apply_overrides(base, ovs)
            label = f"s2-{method}-n{n}-" + "-".join(o.split(".")[-1].replace("=", "") for o in ovs)
            run_experiment(cfg, data, split=split, t_start=t0, t_end=t1, label=label[:80])


def stage3(cfg0: Config, split: str, t0: int, t1: int, base_overrides: list[str]) -> None:
    base = apply_overrides(cfg0, base_overrides)
    data = load_sim_data(base, t_start=t0, t_end=t1)
    for rule in ("hold_to_resolution", "follow_exit"):
        for lat in (1, 5, 15, 30, 60, 240):
            cfg = apply_overrides(base, [f"exit.rule={rule}", f"execution.latency_minutes={lat}"])
            run_experiment(
                cfg,
                data,
                split=split,
                t_start=t0,
                t_end=t1,
                label=f"s3-{rule[:6]}-lat{lat}",
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, required=True, choices=[1, 2, 3])
    ap.add_argument("--split", default="train", choices=sorted(SPLITS))
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--method", default="skill")
    ap.add_argument("--n", type=int, nargs="+", default=[20])
    ap.add_argument("--base-override", action="append", default=[])
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg0 = Config.load(args.config)
    t0, t1 = (ts(d) for d in SPLITS[args.split])
    paths.DATA.mkdir(exist_ok=True)
    if args.stage == 1:
        stage1(cfg0, args.split, t0, t1)
    elif args.stage == 2:
        stage2(cfg0, args.split, t0, t1, args.method, args.n)
    else:
        stage3(cfg0, args.split, t0, t1, args.base_override)
    return 0


if __name__ == "__main__":
    sys.exit(main())
