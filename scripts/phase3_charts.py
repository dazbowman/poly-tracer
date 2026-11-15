"""Phase-3 report figures from the experiments registry + saved runs."""

from __future__ import annotations

import sys

import polars as pl

from polycopy import paths
from polycopy.report.charts import curve_with_ci, equity_curves, roi_by_bucket

REG = paths.DATA / "experiments.parquet"
WINNER_TRAIN = paths.DATA / "runs" / "a35c48e44eb0"  # V1 ungated (train artifacts)
FINAL_VAL = paths.DATA / "runs" / "c87fe433d0c9"  # r2-mmc50 (validation artifacts)


def main() -> int:
    reg = pl.read_parquet(REG).unique(subset=["config_id", "split"], keep="last")

    # decay curve: stage-3 hold_to_resolution latency sweep on the winner base
    s3 = (
        reg.filter(pl.col("config_id").str.starts_with("s3-hold_t-lat"))
        .with_columns(lat=pl.col("config_id").str.extract(r"lat(\d+)").cast(pl.Int64))
        .sort("lat")
    )
    curve_with_ci(
        s3["lat"].to_list(),
        s3["roi"].to_list(),
        s3["roi_ci_lo"].to_list(),
        s3["roi_ci_hi"].to_list(),
        name="phase3_decay",
        title="Latency decay — skill n40 · fresh_flow k3 · 72h (Train)",
        xlabel="copy latency (minutes)",
        log_x=True,
    )

    # capacity curve: ROI vs N at k3/w72 (plus the widened-floors point in text)
    cap_rows = [
        ("s2-winner-k3w72-n40", 40),
        ("r1c-n60-only", 60),
        ("r1a-n80-only", 80),
    ]
    # n20 variant ran as part of stage 2
    n20 = reg.filter(
        pl.col("config_id").str.contains("s2-skill-n20-typefresh_flow-k_min3")
        & pl.col("config_id").str.contains("72")
    )
    xs, rois, los, his = [20], [n20["roi"][0]], [n20["roi_ci_lo"][0]], [n20["roi_ci_hi"][0]]
    for cid, n in cap_rows:
        r = reg.filter(pl.col("config_id") == cid)
        xs.append(n)
        rois.append(r["roi"][0])
        los.append(r["roi_ci_lo"][0])
        his.append(r["roi_ci_hi"][0])
    curve_with_ci(
        xs,
        rois,
        los,
        his,
        name="phase3_capacity",
        title="Capacity — ROI vs watched-wallet count N (k3 · 72h, Train)",
        xlabel="N selected wallets",
    )

    # equity curves: winner on train, final config on validation
    eq = {}
    f = WINNER_TRAIN / "equity_train.parquet"
    if f.exists():
        eq["winner (Train)"] = pl.read_parquet(f)
    f2 = FINAL_VAL / "equity_validation.parquet"
    if f2.exists():
        eq["final gated (Validation)"] = pl.read_parquet(f2)
    if eq:
        equity_curves(eq, "phase3_equity", "Equity — $10k start, winner/final configs")

    # ROI by category and by entry-price bucket (winner train roundtrips)
    rt = pl.read_parquet(WINNER_TRAIN / "roundtrips_train.parquet").filter(
        pl.col("net_pnl").is_not_null()
    )
    by_cat = (
        rt.group_by("category")
        .agg(roi=pl.col("net_pnl").sum() / pl.col("cost").sum(), n=pl.len())
        .sort("n", descending=True)
    )
    roi_by_bucket(
        by_cat, "category", name="phase3_roi_category", title="Train ROI by category (winner)"
    )
    by_px = (
        rt.with_columns(px=(pl.col("entry_price") * 5).floor() / 5)
        .group_by("px")
        .agg(roi=pl.col("net_pnl").sum() / pl.col("cost").sum(), n=pl.len())
        .sort("px")
        .with_columns(bucket=pl.format("{}-{}", pl.col("px"), pl.col("px") + 0.2))
    )
    roi_by_bucket(
        by_px, "bucket", name="phase3_roi_price", title="Train ROI by entry price (winner)"
    )
    print("figures written to", paths.REPORTS / "figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
