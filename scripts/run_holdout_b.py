"""Holdout B: one evaluation of the final config on 2026-04-25 -> 2026-11-14.

d-012/d-020 scope: frozen pool (backfill_api.py), fills = local dataset up to
its end + API trades after; weekly features recomputed per Monday for the pool;
tapes from /v2/prices-history (price-only -> §7.7 thin fallback: last + 1.5c);
PM-US fees. Weaker than the dataset backtest (no taker tape, no post-cutoff
split/merge events) - reported separately from G1 by design.

Steps (each cached/resumable):
  --step features   build data/holdout_b/features/asof=*.parquet
  --step prices     crawl prices-history for watched-condition tokens
  --step run        engine + registry row + roundtrips
  (default: all three in order)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

import polars as pl

from polycopy import paths
from polycopy.backtest.data import load_markets_frame
from polycopy.backtest.engine import MarketMeta, SimData, WalletFills
from polycopy.backtest.experiments import run_experiment
from polycopy.config import Config
from polycopy.sim.fills_backtest import TokenTape
from polycopy.venues.pm_global.data_api import DataApiClient
from polycopy.wallets.features import build_features_asof
from polycopy.wallets.select import select_wallets

log = logging.getLogger("holdout_b")

HB = paths.DATA / "holdout_b"
B_START = "2026-04-25"
B_END = "2026-11-14"
FINAL_OVERRIDES = {"n_wallets": 40, "min_median_market_cost": 50.0}
FINAL_SIGNAL = {"k_min": 3, "flow_window_hours": 72}
DAY = 86_400


def final_config() -> Config:
    base = Config.load(paths.REPO / "configs" / "default.yaml")
    return base.model_copy(
        update={
            "selection": base.selection.model_copy(update=FINAL_OVERRIDES),
            "signal": base.signal.model_copy(update=FINAL_SIGNAL),
        }
    )


def ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def local_cutoff() -> int:
    f = HB / "local_cutoff.txt"
    if f.exists():
        return int(f.read_text())
    cut = (
        pl.scan_parquet(paths.FILLS_DIR / "fills_2026-04_s*.parquet")
        .select(pl.col("ts").max())
        .collect(engine="streaming")
        .item()
    )
    f.write_text(str(cut))
    return cut


def pool_fills() -> pl.LazyFrame:
    """Local fills (ts <= cutoff) + API fills (ts > cutoff), pool wallets only."""
    cut = local_cutoff()
    pool = pl.scan_parquet(HB / "pool.parquet").select("wallet")
    cols = [
        "ts",
        "wallet",
        "condition_id",
        "token_id",
        "canon_dir",
        "shares",
        "usdc",
        "bought",
        "role",
    ]
    loc = (
        pl.scan_parquet(paths.FILLS_DIR / "*.parquet")
        .filter(pl.col("ts") <= cut)
        .join(pool, on="wallet", how="semi")
        .select(cols)
    )
    api = (
        pl.scan_parquet(HB / "api_fills" / "*.parquet")
        .filter(pl.col("ts") > cut)
        .join(pool, on="wallet", how="semi")
        .select(cols)
    )
    return pl.concat([loc, api], how="vertical_relaxed")  # fmt: skip


def b_mondays() -> list[int]:
    d = datetime.strptime(B_START, "%Y-%m-%d").replace(tzinfo=UTC)
    d += timedelta(days=(7 - d.weekday()) % 7)
    end = datetime.strptime(B_END, "%Y-%m-%d").replace(tzinfo=UTC)
    out = []
    while d <= end:
        out.append(int(d.timestamp()))
        d += timedelta(days=7)
    return out


def step_features(cfg: Config) -> None:
    out_dir = HB / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    fills = pool_fills()
    markets = load_markets_frame().lazy()
    events = pl.scan_parquet(paths.DATA / "wallet_events.parquet")  # pre-cutoff only (d-020)
    rets_f = HB / "market_returns.parquet"
    if not rets_f.exists():
        from polycopy.wallets.pnl import market_returns

        t = time.time()
        tmp = rets_f.with_suffix(".tmp")
        market_returns(fills, markets).sink_parquet(tmp, compression="zstd")
        tmp.rename(rets_f)
        log.info("pool market returns done (%.0fs)", time.time() - t)
    mkt_rets_all = pl.scan_parquet(rets_f)
    t0 = time.time()
    for i, asof in enumerate(b_mondays(), 1):
        tag = datetime.fromtimestamp(asof, tz=UTC).strftime("%Y-%m-%d")
        f_out = out_dir / f"asof={tag}.parquet"
        if f_out.exists():
            continue
        df = build_features_asof(
            fills, events, markets, asof_ts=asof, cfg=cfg, mkt_rets_all=mkt_rets_all
        )
        df.write_parquet(f_out, compression="zstd")
        log.info(
            "[%d/%d] %s: %d candidates (%.0fs)",
            i,
            len(b_mondays()),
            tag,
            df.height,
            time.time() - t0,
        )


def b_selections(cfg: Config) -> dict[int, list[str]]:
    sels: dict[int, list[str]] = {}
    for f in sorted((HB / "features").glob("asof=*.parquet")):
        tag = f.stem.split("=")[1]
        asof = ts(tag)
        sel = select_wallets(pl.read_parquet(f), cfg)
        sels[asof] = sel["wallet"].to_list()
    return sels


CHUNK_DAYS = 14  # /v2/prices-history rejects spans > 15 days


def step_prices(cfg: Config) -> None:
    """Crawl 5-min price history for tokens of conditions that can actually
    produce a signal: >= k_min distinct watched wallets with a buy inside one
    flow_window (any direction — a strict superset of fresh_flow signals).
    The engine skips entries whose token has no tape, and non-signalable
    conditions never reach entry, so the omitted tapes are unreachable.
    Spans are clipped to [first watched buy - 7d, resolution + 1d] and chunked
    to the API's 15-day request limit."""
    out = HB / "prices"
    out.mkdir(parents=True, exist_ok=True)
    sels = b_selections(cfg)
    watched = sorted({w for ws in sels.values() for w in ws})
    log.info("%d refreshes, %d watched wallets", len(sels), len(watched))
    t0s, t1s = ts(B_START), ts(B_END)
    buys = (
        pool_fills()
        .filter(
            pl.col("wallet").is_in(watched)
            & (pl.col("ts") >= t0s)
            & (pl.col("ts") < t1s)
            & pl.col("bought")
        )
        .select("condition_id", "wallet", "ts")
        .unique()
        .collect(engine="streaming")
        .sort("condition_id", "ts")
    )
    k, win = cfg.signal.k_min, cfg.signal.flow_window_hours * 3600
    keep: list[str] = []
    for cond, grp in buys.group_by("condition_id", maintain_order=True):
        t = grp["ts"].to_numpy()
        w = grp["wallet"].to_list()
        seen: dict[str, int] = {}
        j = 0
        for i in range(len(t)):
            seen[w[i]] = seen.get(w[i], 0) + 1
            while t[i] - t[j] > win:
                seen[w[j]] -= 1
                if not seen[w[j]]:
                    del seen[w[j]]
                j += 1
            if len(seen) >= k:
                keep.append(cond[0])
                break
    log.info("signalable conditions: %d / %d touched", len(keep), buys["condition_id"].n_unique())
    spans = buys.group_by("condition_id").agg(t_min=pl.col("ts").min())
    mk = (
        load_markets_frame()
        .lazy()
        .filter(pl.col("condition_id").is_in(keep))
        .join(spans.lazy(), on="condition_id", how="inner")
        .select("yes_token_id", "no_token_id", "resolved_ts", "t_min")
        .collect()
    )
    jobs: list[tuple[str, int, int]] = []  # (token, crawl_start, crawl_end)
    for r in mk.iter_rows(named=True):
        c0 = r["t_min"] - 7 * DAY
        c1 = min(t1s, (r["resolved_ts"] // 1000 + DAY) if r["resolved_ts"] else t1s)
        for tok in (r["yes_token_id"], r["no_token_id"]):
            if tok and c0 < c1:
                jobs.append((tok, c0, c1))
    n_req = sum(-(-(c1 - c0) // (CHUNK_DAYS * DAY)) for _, c0, c1 in jobs)
    log.info("tokens to crawl: %d (~%d requests)", len(jobs), n_req)
    api = DataApiClient(cache_dir=paths.CACHE / "data_api")
    done_f = out / "done_tokens.txt"
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    rows: list[dict] = []
    part = len(list(out.glob("part_*.parquet")))
    n_fail = 0
    t0 = time.time()
    for i, (tok, c0, c1) in enumerate(jobs, 1):
        if tok not in done:
            try:
                got: list[dict] = []
                s = c0
                while s < c1:
                    e = min(s + CHUNK_DAYS * DAY, c1)
                    hist = api.prices_history(tok, start=s, end=e, bucket_seconds=300)
                    got.extend({"token_id": tok, **r} for r in hist)
                    s = e
                rows.extend(got)
                done.add(tok)  # failures stay un-done -> retried on the next run
            except Exception as e:
                n_fail += 1
                log.warning("prices-history failed for %s: %s", tok, e)
        if len(rows) >= 200_000 or i == len(jobs):
            if rows:
                pl.DataFrame(rows).write_parquet(out / f"part_{part:03d}.parquet")
                part += 1
                rows = []
            done_f.write_text("\n".join(sorted(done)))
            log.info(
                "prices: %d/%d tokens (%.0fs, %d failed)", i, len(jobs), time.time() - t0, n_fail
            )


def step_run(cfg: Config) -> None:
    t0s, t1s = ts(B_START), ts(B_END)
    sels = b_selections(cfg)
    watched = sorted({w for ws in sels.values() for w in ws})
    med: dict[int, dict[str, float]] = {}
    for f in sorted((HB / "features").glob("asof=*.parquet")):
        asof = ts(f.stem.split("=")[1])
        fe = pl.read_parquet(f)
        md = fe.filter(pl.col("wallet").is_in(sels[asof])).select("wallet", "median_market_cost")
        med[asof] = dict(
            zip(md["wallet"].to_list(), md["median_market_cost"].to_list(), strict=True)
        )

    fills = (
        pool_fills()
        .filter(pl.col("wallet").is_in(watched))
        .select("ts", "wallet", "condition_id", "canon_dir", "bought", "shares", "usdc")
        .collect(engine="streaming")
        .sort("ts")
    )
    log.info("watched fills: %d", fills.height)
    mk_all = load_markets_frame()
    conds = set(fills["condition_id"].unique().to_list())
    mk = mk_all.filter(pl.col("condition_id").is_in(sorted(conds)))
    markets: dict[str, MarketMeta] = {}
    for r in mk.iter_rows(named=True):
        markets[r["condition_id"]] = MarketMeta(
            condition_id=r["condition_id"],
            event_id=r["event_id"] or r["condition_id"],
            yes_token=r["yes_token_id"],
            no_token=r["no_token_id"],
            category=r["category_bucket"],
            neg_risk=bool(r["neg_risk"]),
            end_ts=(r["end_ts"] // 1000) if r["end_ts"] else None,
            resolved_ts=(r["resolved_ts"] // 1000) if r["resolved_ts"] else None,
            payout_yes=r["payout_yes"],
            game_start_ts=(r["game_start_ts"] // 1000) if r["game_start_ts"] else None,
        )
    log.info("markets: %d", len(markets))

    # price-only tapes -> §7.7 thin-fallback execution (taker fields zeroed)
    pr = (
        pl.scan_parquet(HB / "prices" / "part_*.parquet")
        .with_columns(
            bucket_ts=pl.col("timestamp").cast(pl.Int64),
            last_price=pl.col("price").cast(pl.Float64),
        )
        .select("token_id", "bucket_ts", "last_price")
        .unique(subset=["token_id", "bucket_ts"], keep="first")
        .collect(engine="streaming")
        .sort("token_id", "bucket_ts")
    )
    import numpy as np

    tapes: dict[str, TokenTape] = {}
    for tok, grp in pr.group_by("token_id", maintain_order=True):
        n = grp.height
        tapes[tok[0]] = TokenTape(
            bucket_ts=grp["bucket_ts"].to_numpy().astype(np.int64),
            taker_buy_vwap=np.full(n, np.nan),
            taker_sell_vwap=np.full(n, np.nan),
            taker_buy_usdc=np.zeros(n),
            volume_usdc=np.zeros(n),
            last_price=grp["last_price"].to_numpy().astype(float),
        )
    log.info("tapes: %d tokens", len(tapes))

    wallet_fills: dict[str, WalletFills] = {}
    for w, grp in fills.group_by("wallet", maintain_order=True):
        wallet_fills[w[0]] = WalletFills(
            ts=grp["ts"].to_numpy().astype(np.int64),
            condition_id=grp["condition_id"].to_list(),
            is_yes=((grp["canon_dir"] == 1) == grp["bought"]).to_numpy(),
            bought=grp["bought"].to_numpy(),
            shares=grp["shares"].to_numpy().astype(float),
            usdc=grp["usdc"].to_numpy().astype(float),
        )
    data = SimData(
        markets=markets, tapes=tapes, fills=wallet_fills, selections=sels, wallet_median_cost=med
    )
    run_dir = HB / "run"
    run_experiment(
        cfg,
        data,
        split="holdout_b",
        t_start=t0s,
        t_end=t1s,
        run_dir=run_dir,
        label="HOLDOUT-B-final",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=["features", "prices", "run"], default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = final_config()
    steps = [args.step] if args.step else ["features", "prices", "run"]
    for s in steps:
        {"features": step_features, "prices": step_prices, "run": step_run}[s](cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
