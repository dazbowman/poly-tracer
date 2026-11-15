"""Matplotlib chart helpers for phase reports. PNGs into reports/figures/."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

from polycopy import paths

FIG_DIR = paths.REPORTS / "figures"


def _save(fig: plt.Figure, name: str) -> Path:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = FIG_DIR / f"{name}.png"
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def equity_curves(curves: dict[str, pl.DataFrame], name: str, title: str) -> Path:
    """curves: label -> frame with ts (epoch s) and equity."""
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for label, df in curves.items():
        ts = df["ts"].to_numpy().astype("datetime64[s]")
        ax.plot(ts, df["equity"].to_numpy(), label=label, lw=1.4)
    ax.set_ylabel("equity ($)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(alpha=0.3)
    return _save(fig, name)


def curve_with_ci(
    x: list[float],
    roi: list[float],
    lo: list[float],
    hi: list[float],
    *,
    name: str,
    title: str,
    xlabel: str,
    log_x: bool = False,
) -> Path:
    fig, ax = plt.subplots(figsize=(7, 4))
    xa = np.asarray(x, dtype=float)
    ax.plot(xa, roi, "o-", color="tab:blue")
    ax.fill_between(xa, lo, hi, alpha=0.2, color="tab:blue")
    ax.axhline(0, color="k", lw=0.8)
    if log_x:
        ax.set_xscale("log")
        ax.set_xticks(xa)
        ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax.set_xlabel(xlabel)
    ax.set_ylabel("net ROI")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    return _save(fig, name)


def roi_by_bucket(df: pl.DataFrame, bucket_col: str, *, name: str, title: str) -> Path:
    """df: bucket_col, roi, n columns."""
    fig, ax = plt.subplots(figsize=(8, 4))
    labels = df[bucket_col].to_list()
    rois = df["roi"].to_numpy()
    colors = ["tab:green" if r > 0 else "tab:red" for r in rois]
    ax.bar(range(len(labels)), rois, color=colors, alpha=0.75)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    for i, (r, n) in enumerate(zip(rois, df["n"].to_list(), strict=True)):
        ax.annotate(
            f"n={n}", (i, r), textcoords="offset points", xytext=(0, 4), fontsize=7, ha="center"
        )
    ax.set_ylabel("net ROI")
    ax.set_title(title)
    return _save(fig, name)
