"""Experiment runner and registry (SPEC §8.4): one row per (config, split),
keyed by config hash + git SHA, with round-trip metrics and bootstrap CIs."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import polars as pl

from polycopy import paths
from polycopy.backtest.engine import Engine, SimData, SimResult
from polycopy.backtest.stats import brier_score, event_bootstrap_roi, max_drawdown
from polycopy.config import Config

log = logging.getLogger(__name__)

REGISTRY = paths.DATA / "experiments.parquet"


def round_trips(res: SimResult, data: SimData) -> pl.DataFrame:
    """One row per entered trade with its final outcome (resolved or exited)."""
    buys = {t["signal_id"]: t for t in res.trades if t["side"] == "buy" and t["status"] == "open"}
    closes = {t["signal_id"]: t for t in res.trades if t["side"] in ("sell", "resolve")}
    sig = {s["signal_id"]: s for s in res.signals}
    rows = []
    for sid, b in buys.items():
        c = closes.get(sid)
        s = sig[sid]
        cost, fee_in = b["notional"], b["fee"]
        if c is None:
            status, pnl, fees = "open_at_end", None, fee_in
        else:
            fees = fee_in + c["fee"]
            pnl = c["notional"] - cost - fees
            status = c["status"]
        m = data.markets[b["condition_id"]]
        rows.append(
            {
                "signal_id": sid,
                "condition_id": b["condition_id"],
                "event_id": m.event_id,
                "category": m.category,
                "entry_ts": b["ts_exec"],
                "entry_price": b["avg_price"],
                "shares": b["shares"],
                "cost": cost,
                "fees": fees,
                "net_pnl": pnl,
                "status": status,
                "n_wallets": s["n_wallets"],
                "payout_token": (
                    c["avg_price"] if c is not None and c["side"] == "resolve" else None
                ),
                "end_s": m.end_ts,
            }
        )
    return pl.DataFrame(rows)


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=paths.REPO,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def run_experiment(
    cfg: Config,
    data: SimData,
    *,
    split: str,
    t_start: int,
    t_end: int,
    run_dir: Path | None = None,
    label: str = "",
) -> dict:
    t0 = time.time()
    res = Engine(cfg, data, t_start=t_start, t_end=t_end).run()
    rt = round_trips(res, data)
    resolved = (
        rt.filter(pl.col("net_pnl").is_not_null())
        if rt.height
        else pl.DataFrame(
            schema={
                "net_pnl": pl.Float64,
                "cost": pl.Float64,
                "event_id": pl.Utf8,
                "payout_token": pl.Float64,
                "entry_price": pl.Float64,
            }
        )
    )
    boot = event_bootstrap_roi(
        resolved,
        n_resamples=cfg.stats.bootstrap_resamples,
        seed=cfg.seed,
    )
    eq = pl.DataFrame(res.equity_daily)
    dd = max_drawdown(eq["equity"].to_numpy()) if eq.height else 0.0
    res_only = resolved.filter(pl.col("payout_token").is_not_null())
    brier = (
        brier_score(
            res_only["entry_price"].to_numpy(),
            res_only["payout_token"].to_numpy(),
        )
        if res_only.height
        else float("nan")
    )
    row = {
        "config_id": label or cfg.config_hash(),
        "config_hash": cfg.config_hash(),
        "split": split,
        "n_signals": len(res.signals),
        "n_trades": boot.n_trades,
        "n_events": boot.n_events,
        "unfillable": res.unfillable,
        "roi": boot.roi,
        "roi_ci_lo": boot.ci_lo,
        "roi_ci_hi": boot.ci_hi,
        "p_one_sided": boot.p_one_sided,
        "pnl": float(resolved["net_pnl"].sum()) if resolved.height else 0.0,
        "max_dd": dd,
        "brier": brier,
        "hit_rate": float((res_only["net_pnl"] > 0).mean()) if res_only.height else None,
        "final_equity": res.final_equity,
        "runtime_s": round(time.time() - t0, 1),
        "git_sha": _git_sha(),
        "ts": int(time.time()),
    }
    _append_registry(row)
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
        if rt.height:
            rt.write_parquet(run_dir / f"roundtrips_{split}.parquet")
        if eq.height:
            eq.write_parquet(run_dir / f"equity_{split}.parquet")
        if res.signals:
            pl.DataFrame(res.signals).write_parquet(run_dir / f"signals_{split}.parquet")
    log.info(
        "[%s/%s] trades=%d events=%d roi=%.3f ci=(%.3f,%.3f) p=%.3f unfillable=%d (%.0fs)",
        row["config_id"],
        split,
        row["n_trades"],
        row["n_events"],
        row["roi"],
        row["roi_ci_lo"],
        row["roi_ci_hi"],
        row["p_one_sided"],
        row["unfillable"],
        row["runtime_s"],
    )
    return row


def _append_registry(row: dict) -> None:
    df = pl.DataFrame([row])
    if REGISTRY.exists():
        old = pl.read_parquet(REGISTRY)
        df = pl.concat([old, df], how="diagonal")
    df.write_parquet(REGISTRY)
