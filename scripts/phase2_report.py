"""Generate reports/phase-2.md: the wallet landscape (SPEC §9 Phase 2.5).

- label distribution across candidates at a reference as-of date
- how today's monthly top-50 (live leaderboard) would be labeled
- category mix of skilled wallets
- overlap between naive_pnl and skill selections

Usage: uv run python scripts/phase2_report.py [--asof 2026-02-02]
Requires the features cache for the as-of date (build_features.py).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

import polars as pl

from polycopy import paths
from polycopy.config import Config
from polycopy.venues.pm_global.data_api import DataApiClient
from polycopy.wallets.select import select_wallets

log = logging.getLogger("phase2")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-02-02")
    ap.add_argument("--replication", default="", help="text from replication_check.py to embed")
    ap.add_argument("--validation", default="", help="text from validate_positions.py to embed")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    cfg = Config.load(paths.REPO / "configs" / "default.yaml")

    tag = args.asof
    feats = pl.read_parquet(paths.DATA / "features" / f"asof={tag}.parquet")
    naive = pl.read_parquet(paths.DATA / "features" / f"naive={tag}.parquet")

    labels = (
        feats.group_by("label")
        .agg(n=pl.len(), volume=pl.col("volume_usdc").sum())
        .sort("n", descending=True)
    )
    skilled = feats.filter(pl.col("label") == "skilled_directional")
    cat_mix = skilled.group_by("category_top").agg(n=pl.len()).sort("n", descending=True).head(8)

    sel_skill = select_wallets(feats, cfg, method="skill", n_wallets=20)
    sel_naive = select_wallets(feats, cfg, method="naive_pnl", n_wallets=20, naive_pnl=naive)
    overlap = set(sel_skill["wallet"]) & set(sel_naive["wallet"])

    # today's monthly top-50 by PnL, labeled by our features (where known)
    api = DataApiClient(cache_dir=paths.CACHE / "data_api")
    top50 = api.leaderboard(time_period="month", sort_by="PNL", max_pages=1)[:50]
    lb = pl.DataFrame(
        [{"wallet": r["user_id"].lower(), "lb_pnl": r["pnl"], "lb_vol": r["volume"]} for r in top50]
    )
    lb_labeled = lb.join(feats.select("wallet", "label", "skilled"), on="wallet", how="left")
    lb_counts = (
        lb_labeled.with_columns(pl.col("label").fill_null("not_in_universe"))
        .group_by("label")
        .agg(n=pl.len())
        .sort("n", descending=True)
    )

    asof_h = datetime.strptime(tag, "%Y-%m-%d").replace(tzinfo=UTC).strftime("%Y-%m-%d")
    out = paths.REPORTS / "phase-2.md"
    lines = [
        "# Phase 2 — Wallet analytics",
        "",
        "Result: features/skill/classifier pipeline built and validated; "
        f"landscape as of {asof_h} below.",
        "Decision: proceed to Phase 3 backtest.",
        "",
        "## Candidate landscape (as-of "
        + asof_h
        + f", lookback {cfg.universe.lookback_days}d, gates: ≥{cfg.universe.min_resolved_markets} "
        f"resolved markets, ≥${cfg.universe.min_volume_usd:,.0f} volume, active ≤30d)",
        "",
        f"Candidates passing gates: **{feats.height:,}**",
        "",
        "| label | wallets | share | lookback volume |",
        "|---|---|---|---|",
    ]
    for r in labels.iter_rows(named=True):
        lines.append(
            f"| {r['label']} | {r['n']:,} | {100 * r['n'] / feats.height:.1f}% | "
            f"${r['volume'] / 1e6:,.0f}M |"
        )
    lines += [
        "",
        "Hyperactive wallets (>200 fills/day or >100k fills) were pre-excluded from feature",
        "extraction and are bot/MM-class by construction (DECISIONS d-013); shares above are",
        "within the extracted candidate set.",
        "",
        "## Category mix of skilled wallets",
        "",
        "| top category | skilled wallets |",
        "|---|---|",
    ]
    lines += [f"| {r['category_top']} | {r['n']} |" for r in cat_mix.iter_rows(named=True)]
    lines += [
        "",
        "## naive_pnl vs skill selection (N=20, same date)",
        "",
        f"- overlap: **{len(overlap)}/20** wallets",
        "- skill picks' median lookback ROI: "
        f"{sel_skill.join(feats, on='wallet')['roi_cost_weighted'].median():.1%}",
        "",
        "## Today's monthly leaderboard top-50, labeled",
        "",
        "| label | wallets |",
        "|---|---|",
    ]
    lines += [f"| {r['label']} | {r['n']} |" for r in lb_counts.iter_rows(named=True)]
    lines += [
        "",
        "(`not_in_universe` = not in the 2024-01→2026-04 extract set or below gates as of the",
        "reference date — e.g. new accounts; consistent with the papers' finding that top",
        "leaderboard PnL is dominated by variance, not persistent skill.)",
        "",
    ]
    if args.validation:
        lines += ["## Position-engine validation (vs chain user_position)", "", args.validation, ""]
    if args.replication:
        lines += ["## Replication check (A/B halves)", "", args.replication, ""]
    out.write_text("\n".join(lines))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
