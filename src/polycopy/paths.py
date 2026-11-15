"""Canonical locations for local data (gitignored) and reports."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
CACHE = DATA / "cache"
REPORTS = REPO / "reports"

MARKETS_PARQUET = DATA / "markets.parquet"
WALLET_MAP_PARQUET = DATA / "wallet_map.parquet"
UNIVERSE_DIR = DATA / "universe"
FILLS_DIR = DATA / "fills_norm"
PRICES_DIR = DATA / "prices_5m"

HF_BASE = "hf://datasets/moose-code/polymarket-onchain-v1"
