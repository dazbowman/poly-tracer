"""Load SimData for a backtest run from the local data lake.

Flow: weekly features cache -> selection series (method, N) -> watched wallet
union -> their fills -> the touched conditions' two token tapes. Everything in
epoch seconds inside the engine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np
import polars as pl

from polycopy import paths
from polycopy.backtest.engine import MarketMeta, SimData, WalletFills
from polycopy.config import Config
from polycopy.sim.fills_backtest import TokenTape
from polycopy.wallets.select import select_wallets

log = logging.getLogger(__name__)


SIM_MARKET_COLS = (
    "condition_id",
    "event_id",
    "yes_token_id",
    "no_token_id",
    "category_bucket",
    "neg_risk",
    "end_ts",
    "resolved_ts",
    "payout_yes",
    "game_start_ts",
)


def load_markets_frame() -> pl.DataFrame:
    """Sim-relevant catalog columns only: the full 5.6M-row catalog with its
    question/slug/tag strings is several GB in memory and OOMs repeated loads."""
    gamma = pl.scan_parquet(paths.MARKETS_PARQUET).select(SIM_MARKET_COLS)
    chain = pl.scan_parquet(paths.DATA / "chain_condition_map.parquet").select(
        "condition_id", chain_payout=pl.col("payout_yes")
    )
    return (
        gamma.join(chain, on="condition_id", how="left")
        .with_columns(payout_yes=pl.coalesce(pl.col("payout_yes"), pl.col("chain_payout")))
        .drop("chain_payout")
        .collect(engine="streaming")
    )


def selection_series(
    cfg: Config,
    *,
    t_start: int,
    t_end: int,
    method: str | None = None,
    n_wallets: int | None = None,
) -> tuple[dict[int, list[str]], dict[int, dict[str, float]]]:
    """Selections and median-cost maps for every cached refresh date in range.

    Refresh dates whose features predate t_start by up to 8 days are included so
    the sim starts with a selection in force.
    """
    feats_dir = paths.DATA / "features"
    sels: dict[int, list[str]] = {}
    med: dict[int, dict[str, float]] = {}
    for f in sorted(feats_dir.glob("asof=*.parquet")):
        tag = f.stem.split("=")[1]
        asof = int(datetime.strptime(tag, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
        if asof < t_start - 8 * 86_400 or asof > t_end:
            continue
        feats = pl.read_parquet(f)
        naive = None
        nf = feats_dir / f"naive={tag}.parquet"
        if (method or cfg.selection.method) == "naive_pnl":
            naive = pl.read_parquet(nf)
        sel = select_wallets(feats, cfg, method=method, n_wallets=n_wallets, naive_pnl=naive)
        wallets = sel["wallet"].to_list()
        sels[asof] = wallets
        md = feats.filter(pl.col("wallet").is_in(wallets)).select("wallet", "median_market_cost")
        med[asof] = dict(
            zip(md["wallet"].to_list(), md["median_market_cost"].to_list(), strict=True)
        )
    return sels, med


def load_sim_data(
    cfg: Config,
    *,
    t_start: int,
    t_end: int,
    method: str | None = None,
    n_wallets: int | None = None,
) -> SimData:
    sels, med = selection_series(
        cfg, t_start=t_start, t_end=t_end, method=method, n_wallets=n_wallets
    )
    watched = sorted({w for ws in sels.values() for w in ws})
    log.info("selection series: %d refreshes, %d watched wallets", len(sels), len(watched))

    fills = (
        pl.scan_parquet(paths.FILLS_DIR / "*.parquet")
        .filter(pl.col("wallet").is_in(watched))
        .select("ts", "wallet", "condition_id", "canon_dir", "bought", "shares", "usdc")
        .collect()
        .sort("ts")
    )
    log.info("watched fills: %d rows", fills.height)

    mk = load_markets_frame()
    conds = set(fills["condition_id"].unique().to_list())
    mk_used = mk.filter(pl.col("condition_id").is_in(sorted(conds)))
    markets: dict[str, MarketMeta] = {}
    tokens: set[str] = set()
    for r in mk_used.iter_rows(named=True):
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
        tokens.add(r["yes_token_id"])
        tokens.add(r["no_token_id"])
    log.info("markets: %d used (of %d catalog)", len(markets), mk.height)

    prices = (
        pl.scan_parquet(paths.PRICES_DIR / "*.parquet")
        .filter(pl.col("token_id").is_in(sorted(tokens)))
        .select(
            "token_id",
            "bucket_ts",
            "taker_buy_vwap",
            "taker_sell_vwap",
            "taker_buy_usdc",
            "volume_usdc",
            "last_price",
        )
        .collect(engine="streaming")
        .sort("token_id", "bucket_ts")
    )
    tapes: dict[str, TokenTape] = {}
    for tok, grp in prices.group_by("token_id", maintain_order=True):
        tapes[tok[0]] = TokenTape(
            bucket_ts=grp["bucket_ts"].to_numpy().astype(np.int64),
            taker_buy_vwap=grp["taker_buy_vwap"].to_numpy().astype(float),
            taker_sell_vwap=grp["taker_sell_vwap"].to_numpy().astype(float),
            taker_buy_usdc=grp["taker_buy_usdc"].fill_null(0.0).to_numpy().astype(float),
            volume_usdc=grp["volume_usdc"].to_numpy().astype(float),
            last_price=grp["last_price"].to_numpy().astype(float),
        )
    log.info("tapes: %d tokens", len(tapes))

    wallet_fills: dict[str, WalletFills] = {}
    for w, grp in fills.group_by("wallet", maintain_order=True):
        is_yes = (grp["canon_dir"] == 1) == grp["bought"]
        wallet_fills[w[0]] = WalletFills(
            ts=grp["ts"].to_numpy().astype(np.int64),
            condition_id=grp["condition_id"].to_list(),
            is_yes=is_yes.to_numpy(),
            bought=grp["bought"].to_numpy(),
            shares=grp["shares"].to_numpy().astype(float),
            usdc=grp["usdc"].to_numpy().astype(float),
        )
    return SimData(
        markets=markets,
        tapes=tapes,
        fills=wallet_fills,
        selections=sels,
        wallet_median_cost=med,
    )
