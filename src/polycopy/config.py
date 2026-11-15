"""Typed configuration mirroring configs/*.yaml (spec SPEC.md section 10).

Every experiment is identified by the hash of its resolved config, so the
models are frozen and `extra="forbid"` rejects misspelled keys instead of
silently ignoring them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class UniverseCfg(_Frozen):
    lookback_days: int = 180
    min_resolved_markets: int = 30
    min_volume_usd: float = 25_000
    active_within_days: int = 30


class SelectionCfg(_Frozen):
    method: Literal["skill", "naive_pnl"] = "skill"
    n_wallets: int = 20
    refresh: Literal["weekly"] = "weekly"
    fdr_q: float = 0.10
    shrink_n0: int = 30
    per_category: bool = False
    min_category_markets: int = 15
    # d-020 copyability gate: a wallet's lookback median per-market buy cost
    # must reach this for it to be selectable (0 = off). Dust-bet longshot
    # farmers score high but cannot form $-level consensus.
    min_median_market_cost: float = 0.0


class FiltersCfg(_Frozen):
    mm_two_sided_share_max: float = 0.30
    mm_split_merge_share_max: float = 0.20
    mm_volume_to_abs_pnl_max: float = 200
    bot_short_crypto_share_max: float = 0.30
    one_hit_top_market_share_max: float = 0.50


class SignalCfg(_Frozen):
    type: Literal["holdings", "fresh_flow", "single_wallet"] = "fresh_flow"
    k_min: int = 2
    flow_window_hours: int = 24
    min_wallet_position_usd: float = 500
    min_wallet_add_usd: float = 250
    min_conviction_ratio: float = 1.0
    chase_limit_cents: float | None = 5
    price_min: float = 0.05
    price_max: float = 0.92
    min_time_to_resolution_hours: float = 6
    max_time_to_resolution_days: float = 120
    exclude_categories: tuple[str, ...] = ("crypto_short",)
    conflict_rule: Literal["net"] = "net"


class SizingCfg(_Frozen):
    per_trade_pct: float = 2.0
    max_event_pct: float = 6.0
    max_deployed_pct: float = 60
    max_book_participation: float = 0.25
    min_order_usd: float = 50


class ExitCfg(_Frozen):
    rule: Literal["hold_to_resolution", "follow_exit"] = "hold_to_resolution"
    follow_exit_threshold: float = 0.5


class ExecutionCfg(_Frozen):
    latency_minutes: int = 30
    backtest_window_minutes: int = 10
    backtest_penalty_cents: float = 1.0
    unfillable_after_minutes: int = 60
    venue_priority: tuple[str, ...] = ("polymarket_us", "kalshi")
    reference_venue: str = "polymarket_global"


class PmUsFeeCfg(_Frozen):
    theta: float = 0.095
    rounding: Literal["bankers_cent"] = "bankers_cent"


class KalshiFeeCfg(_Frozen):
    coeff: float = 0.07
    rounding: Literal["ceil_cent"] = "ceil_cent"


class PmGlobalFeeCfg(_Frozen):
    source: Literal["clob_fee_rate"] = "clob_fee_rate"


class FeesCfg(_Frozen):
    polymarket_us: PmUsFeeCfg = PmUsFeeCfg()
    kalshi: KalshiFeeCfg = KalshiFeeCfg()
    polymarket_global: PmGlobalFeeCfg = PmGlobalFeeCfg()


class StatsCfg(_Frozen):
    bootstrap_resamples: int = 10_000
    block: Literal["event"] = "event"


class Config(_Frozen):
    bankroll_usd: float = 10_000
    universe: UniverseCfg = UniverseCfg()
    selection: SelectionCfg = SelectionCfg()
    filters: FiltersCfg = FiltersCfg()
    signal: SignalCfg = SignalCfg()
    sizing: SizingCfg = SizingCfg()
    exit: ExitCfg = ExitCfg()
    execution: ExecutionCfg = ExecutionCfg()
    fees: FeesCfg = FeesCfg()
    stats: StatsCfg = StatsCfg()
    seed: int = 1729

    @classmethod
    def load(cls, path: str | Path) -> Config:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return cls.model_validate(raw)

    def config_hash(self) -> str:
        """Stable 12-hex-char id of the resolved config, for the experiments registry."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]
