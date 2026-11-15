"""Sim-time backtest engine (SPEC §9 Phase 3.1).

15-minute decision ticks; weekly selection refresh; signals S1/S2/S3 over the
selected wallets' canonical positions; §7.4 guards; §7.5 sizing; §7.7 tape
execution; E1/E2 exits; daily equity.

Leakage discipline: every decision consumes data strictly before the tick via
as-of lookups. The only forward-looking reads are (a) the §7.7 execution window
after t_exec, which is the documented fill approximation, not a decision input,
and (b) a position's settlement payout read at resolution time. The decision
log records every signal and order so the leakage test can compare runs
byte-for-byte.
"""

from __future__ import annotations

import bisect
import logging
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from polycopy.config import Config
from polycopy.sim.fees import pm_us_fee
from polycopy.sim.fills_backtest import TokenTape, simulate_buy, simulate_sell
from polycopy.wallets.positions import WalletBook

log = logging.getLogger(__name__)

TICK_S = 15 * 60
DAY_S = 86_400


@dataclass(frozen=True)
class MarketMeta:
    condition_id: str
    event_id: str
    yes_token: str
    no_token: str
    category: str
    neg_risk: bool
    end_ts: int | None  # epoch s
    resolved_ts: int | None
    payout_yes: float | None
    game_start_ts: int | None = None


@dataclass
class WalletFills:
    """One wallet's fills, time-sorted arrays (epoch s)."""

    ts: np.ndarray
    condition_id: list[str]
    is_yes: np.ndarray  # bool
    bought: np.ndarray  # bool
    shares: np.ndarray
    usdc: np.ndarray


@dataclass
class SimData:
    markets: dict[str, MarketMeta]
    tapes: dict[str, TokenTape]  # by token_id
    fills: dict[str, WalletFills]  # by wallet
    selections: dict[int, list[str]]  # refresh_ts (epoch s) -> wallets, ascending keys
    wallet_median_cost: dict[int, dict[str, float]]  # refresh_ts -> wallet -> median cost

    def price_yes_at(self, m: MarketMeta, ts: int) -> float | None:
        tape = self.tapes.get(m.yes_token)
        if tape is not None:
            p = tape.last_trade_at(ts)
            if p is not None:
                return p
        tape = self.tapes.get(m.no_token)
        if tape is not None:
            p = tape.last_trade_at(ts)
            if p is not None:
                return 1.0 - p
        return None


@dataclass
class OpenPosition:
    signal_id: int
    condition_id: str
    direction: int  # +1 long yes-token, -1 long no-token
    token_id: str
    shares: float
    entry_price: float  # price of the token actually bought
    cost: float
    fee: float
    entry_ts: int
    signal_wallets: tuple[str, ...]
    peak_signal_cost: float = 0.0


@dataclass
class SimResult:
    signals: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    equity_daily: list[dict] = field(default_factory=list)
    decision_log: list[str] = field(default_factory=list)
    unfillable: int = 0
    final_equity: float = 0.0


class Engine:
    def __init__(self, cfg: Config, data: SimData, *, t_start: int, t_end: int) -> None:
        self.cfg = cfg
        self.data = data
        self.t_start = t_start
        self.t_end = t_end
        self.cash = cfg.bankroll_usd
        self.positions: dict[str, OpenPosition] = {}  # by condition_id
        self.event_exposure: dict[str, float] = defaultdict(float)
        self.books: dict[str, WalletBook] = {}
        self.fill_ptr: dict[str, int] = {}
        # (wallet, cond) -> [(ts, signed_usdc, signed_shares, yes_px*shares)]
        self.flows: dict[tuple[str, str], list[tuple[int, float, float, float]]] = defaultdict(list)
        self.episode_entered: dict[tuple[str, int], bool] = {}
        self.watched: list[str] = []
        self.median_cost: dict[str, float] = {}
        self.refresh_keys = sorted(data.selections)
        self.next_signal_id = 1
        self.res = SimResult()
        self._resolution_queue = sorted(
            (m.resolved_ts, cid)
            for cid, m in data.markets.items()
            if m.resolved_ts and m.payout_yes is not None
        )
        self._res_ptr = 0

    # ---------------------------------------------------------------- wallets
    def _refresh_selection(self, tick: int) -> set[str]:
        """Apply the latest selection; returns conditions to re-evaluate."""
        i = bisect.bisect_right(self.refresh_keys, tick) - 1
        if i < 0:
            return set()
        key = self.refresh_keys[i]
        wallets = self.data.selections[key]
        if wallets == self.watched:
            return set()
        for w in wallets:
            if w not in self.books:
                self.books[w] = WalletBook()
                self.fill_ptr[w] = 0
                self._advance_wallet(w, tick)
        self.watched = wallets
        self.median_cost = self.data.wallet_median_cost.get(key, {})
        self.res.decision_log.append(f"select {tick} {','.join(wallets)}")
        # membership changed: every condition any watched wallet holds needs
        # a fresh consensus count
        touched: set[str] = set()
        for w in wallets:
            touched.update(self.books[w].conditions.keys())
        return touched

    def _advance_wallet(self, w: str, tick: int) -> set[str]:
        """Apply wallet fills with ts < tick; return touched condition_ids."""
        wf = self.data.fills.get(w)
        touched: set[str] = set()
        if wf is None:
            return touched
        i = self.fill_ptr[w]
        n = len(wf.ts)
        while i < n and wf.ts[i] < tick:
            cond = wf.condition_id[i]
            is_yes, bought = bool(wf.is_yes[i]), bool(wf.bought[i])
            shares, usdc = float(wf.shares[i]), float(wf.usdc[i])
            self.books[w].apply_fill(cond, is_yes=is_yes, bought=bought, shares=shares, usdc=usdc)
            canon_dir = 1 if bought == is_yes else -1
            price = usdc / shares if shares > 0 else 0.0
            yes_px = price if is_yes else 1.0 - price
            self.flows[(w, cond)].append(
                (int(wf.ts[i]), canon_dir * usdc, canon_dir * shares, yes_px * shares)
            )
            touched.add(cond)
            i += 1
        self.fill_ptr[w] = i
        return touched

    # ---------------------------------------------------------------- signals
    def _wallet_signal_dir(self, w: str, cond: str, tick: int) -> int:
        """This wallet's qualifying signal direction for `cond` (0 if none)."""
        sc = self.cfg.signal
        book = self.books.get(w)
        if book is None or cond not in book.conditions:
            return 0
        pos = book.conditions[cond]
        if sc.type in ("holdings", "single_wallet"):
            net = pos.net_yes_shares
            if abs(net) < 1e-9:
                return 0
            side = pos.yes if net > 0 else pos.no
            cost = side.shares * side.avg_price
            if cost < sc.min_wallet_position_usd:
                return 0
            med = self.median_cost.get(w)
            if med and med > 0 and cost / med < sc.min_conviction_ratio:
                return 0
            return 1 if net > 0 else -1
        # fresh_flow: canonical net adds within the window
        window_start = tick - sc.flow_window_hours * 3600
        flow = self.flows.get((w, cond))
        if not flow:
            return 0
        f_net = sum(v for ts, v, _, _ in flow if ts >= window_start)
        if abs(f_net) < sc.min_wallet_add_usd:
            return 0
        d = 1 if f_net > 0 else -1
        # >= 25% growth of the pre-window canonical cost on that side
        side = self.books[w].conditions[cond].yes if d > 0 else self.books[w].conditions[cond].no
        cost_now = side.shares * side.avg_price
        prior = max(cost_now - abs(f_net), 0.0)
        if prior > 0 and abs(f_net) / prior < 0.25:
            return 0
        return d

    def _window_add_vwap(self, w: str, cond: str, d: int, tick: int) -> tuple[float, float]:
        """(cost, yes-equiv vwap) of this wallet's direction-d adds in the flow window."""
        window_start = tick - self.cfg.signal.flow_window_hours * 3600
        flow = self.flows.get((w, cond), [])
        usdc = shares = px_sh = 0.0
        for ts, v, s, pxs in flow:
            if ts >= window_start and (1 if v > 0 else -1) == d and v * d > 0:
                usdc += abs(v)
                shares += abs(s)
                px_sh += pxs
        if shares <= 0:
            return 0.0, float("nan")
        return usdc, px_sh / shares

    def _consensus(self, cond: str, tick: int) -> tuple[int, list[str], float]:
        """(net direction count, contributing wallets, their entry vwap in yes terms).

        Chase-guard reference (SPEC §7.4): S1/S3 use the signalers' average
        cost; S2 uses the VWAP of their window adds.
        """
        fresh = self.cfg.signal.type == "fresh_flow"
        longs, shorts = [], []
        entries: dict[int, list[tuple[float, float]]] = {1: [], -1: []}  # d -> (cost, px)
        for w in self.watched:
            d = self._wallet_signal_dir(w, cond, tick)
            if d == 0:
                continue
            (longs if d > 0 else shorts).append(w)
            if fresh:
                cost, px = self._window_add_vwap(w, cond, d, tick)
                if cost > 0 and np.isfinite(px):
                    entries[d].append((cost, px))
            else:
                pos = self.books[w].conditions.get(cond)
                if pos is not None:
                    e = pos.canonical_entry_price
                    side = pos.yes if d > 0 else pos.no
                    if e is not None and side.shares > 0:
                        entries[d].append((side.shares * side.avg_price, e))
        net = len(longs) - len(shorts)
        d = 1 if net > 0 else -1
        wallets = longs if net > 0 else shorts
        side_entries = entries[d]
        vwap = (
            sum(c * e for c, e in side_entries) / sum(c for c, _ in side_entries)
            if side_entries
            else float("nan")
        )
        return net, wallets, vwap

    # ---------------------------------------------------------------- orders
    def _guards_and_size(
        self, m: MarketMeta, direction: int, wallets_vwap_yes: float, tick: int
    ) -> tuple[float, float, float] | None:
        """Return (notional, token_price_now, chase_cap_token) or None."""
        sc, sz = self.cfg.signal, self.cfg.sizing
        if m.category in sc.exclude_categories:
            return None
        if m.game_start_ts and tick >= m.game_start_ts:
            return None  # live in-play
        if m.end_ts is None:
            return None
        ttr_h = (m.end_ts - tick) / 3600
        if not (sc.min_time_to_resolution_hours <= ttr_h <= sc.max_time_to_resolution_days * 24):
            return None
        p_yes = self.data.price_yes_at(m, tick)
        if p_yes is None:
            return None
        token_price = p_yes if direction > 0 else 1.0 - p_yes
        if not (sc.price_min <= token_price <= sc.price_max):
            return None
        chase_cap = float("inf")
        if sc.chase_limit_cents is not None:
            if not np.isfinite(wallets_vwap_yes):
                return None
            vwap_token = wallets_vwap_yes if direction > 0 else 1.0 - wallets_vwap_yes
            chase_cap = vwap_token + sc.chase_limit_cents / 100.0
            if token_price > chase_cap:
                return None
        equity = self._equity(tick)
        notional = equity * sz.per_trade_pct / 100.0
        event_room = equity * sz.max_event_pct / 100.0 - self.event_exposure[m.event_id]
        deployed = sum(p.cost for p in self.positions.values())
        deploy_room = equity * sz.max_deployed_pct / 100.0 - deployed
        # keep ~3% headroom for the taker fee so cash never goes negative
        notional = min(notional, event_room, deploy_room, self.cash * 0.97)
        tape = self.data.tapes.get(m.yes_token if direction > 0 else m.no_token)
        if tape is not None:
            trail = tape.trailing_volume(tick, 1.0)
            notional = min(notional, sz.max_book_participation * trail)
        if notional < sz.min_order_usd:
            return None
        return notional, token_price, chase_cap

    def _enter(
        self, m: MarketMeta, direction: int, wallets: list[str], vwap_yes: float, tick: int
    ) -> bool:
        """Attempt the entry. True = an order was placed (consumes the episode);
        False = a guard blocked it, leaving the episode open for retry, which is
        what the live paper trader would do on its next tick."""
        sized = self._guards_and_size(m, direction, vwap_yes, tick)
        if sized is None:
            return False
        notional, price_now, chase_cap = sized
        token = m.yes_token if direction > 0 else m.no_token
        tape = self.data.tapes.get(token)
        if tape is None:
            return False
        ex = self.cfg.execution
        t_exec = tick + ex.latency_minutes * 60
        sid = self.next_signal_id
        self.next_signal_id += 1
        self.res.decision_log.append(
            f"signal {tick} {m.condition_id} dir={direction} n={len(wallets)} "
            f"notional={notional:.2f} cap={chase_cap:.4f}"
        )
        self.res.signals.append(
            {
                "signal_id": sid,
                "ts": tick,
                "condition_id": m.condition_id,
                "event_id": m.event_id,
                "category": m.category,
                "canon_dir": direction,
                "n_wallets": len(wallets),
                "wallets": ",".join(sorted(wallets)),
                "wallets_vwap_yes": vwap_yes,
                "price_at_signal": price_now,  # token-frame price from _guards_and_size
                "notional_intent": notional,
            }
        )
        fill = simulate_buy(
            tape,
            t_exec=t_exec,
            notional_usd=notional,
            window_minutes=ex.backtest_window_minutes,
            penalty=ex.backtest_penalty_cents / 100.0,
            unfillable_after_minutes=ex.unfillable_after_minutes,
            price_cap=None if np.isinf(chase_cap) else chase_cap,
        )
        if fill is None:
            self.res.unfillable += 1
            self.res.trades.append(
                {
                    "signal_id": sid,
                    "ts_exec": t_exec,
                    "condition_id": m.condition_id,
                    "side": "buy",
                    "status": "unfillable",
                }
            )
            return True
        shares = notional / fill.price
        fee = pm_us_fee(shares, fill.price, self.cfg.fees.polymarket_us.theta)
        self.cash -= notional + fee
        pos = OpenPosition(
            signal_id=sid,
            condition_id=m.condition_id,
            direction=direction,
            token_id=token,
            shares=shares,
            entry_price=fill.price,
            cost=notional,
            fee=fee,
            entry_ts=t_exec,
            signal_wallets=tuple(sorted(wallets)),
            peak_signal_cost=self._signal_cost(m.condition_id, wallets, direction),
        )
        self.positions[m.condition_id] = pos
        self.event_exposure[m.event_id] += notional
        self.res.trades.append(
            {
                "signal_id": sid,
                "ts_exec": t_exec,
                "condition_id": m.condition_id,
                "side": "buy",
                "shares": shares,
                "avg_price": fill.price,
                "fee": fee,
                "notional": notional,
                "fill_quality": fill.fill_quality,
                "status": "open",
            }
        )
        return True

    def _signal_cost(self, cond: str, wallets: tuple[str, ...] | list[str], d: int) -> float:
        total = 0.0
        for w in wallets:
            book = self.books.get(w)
            if book and cond in book.conditions:
                side = book.conditions[cond].yes if d > 0 else book.conditions[cond].no
                total += side.shares * side.avg_price
        return total

    # ---------------------------------------------------------------- exits
    def _check_exits(self, tick: int) -> None:
        if self.cfg.exit.rule != "follow_exit":
            return
        for cond, pos in list(self.positions.items()):
            cur = self._signal_cost(cond, pos.signal_wallets, pos.direction)
            pos.peak_signal_cost = max(pos.peak_signal_cost, cur)
            if pos.peak_signal_cost <= 0:
                continue
            if cur / pos.peak_signal_cost < self.cfg.exit.follow_exit_threshold:
                self._exit_position(pos, tick, reason="follow_exit")

    def _exit_position(self, pos: OpenPosition, tick: int, reason: str) -> None:
        tape = self.data.tapes.get(pos.token_id)
        ex = self.cfg.execution
        fill = (
            simulate_sell(
                tape,
                t_exec=tick + ex.latency_minutes * 60,  # exits pay latency too
                notional_usd=pos.shares * pos.entry_price,
                window_minutes=ex.backtest_window_minutes,
                penalty=ex.backtest_penalty_cents / 100.0,
                unfillable_after_minutes=ex.unfillable_after_minutes,
            )
            if tape is not None
            else None
        )
        if fill is None:
            return  # cannot exit now; hold to resolution
        proceeds = pos.shares * fill.price
        fee = pm_us_fee(pos.shares, fill.price, self.cfg.fees.polymarket_us.theta)
        self.cash += proceeds - fee
        self.event_exposure[self.data.markets[pos.condition_id].event_id] -= pos.cost
        del self.positions[pos.condition_id]
        self.res.decision_log.append(f"exit {tick} {pos.condition_id} {reason}")
        self.res.trades.append(
            {
                "signal_id": pos.signal_id,
                "ts_exec": tick,
                "condition_id": pos.condition_id,
                "side": "sell",
                "shares": pos.shares,
                "avg_price": fill.price,
                "fee": fee,
                "notional": proceeds,
                "fill_quality": fill.fill_quality,
                "status": reason,
            }
        )

    def _settle_resolutions(self, tick: int) -> None:
        # resolution queue holds epoch-second timestamps (SimData loader converts)
        q = self._resolution_queue
        while self._res_ptr < len(q) and q[self._res_ptr][0] <= tick:
            _, cond = q[self._res_ptr]
            self._res_ptr += 1
            pos = self.positions.get(cond)
            if pos is None:
                continue
            m = self.data.markets[cond]
            payout_token = m.payout_yes if pos.direction > 0 else 1.0 - m.payout_yes
            self.cash += pos.shares * payout_token
            self.event_exposure[m.event_id] -= pos.cost
            del self.positions[cond]
            self.res.trades.append(
                {
                    "signal_id": pos.signal_id,
                    "ts_exec": m.resolved_ts,
                    "condition_id": cond,
                    "side": "resolve",
                    "shares": pos.shares,
                    "avg_price": payout_token,
                    "fee": 0.0,
                    "notional": pos.shares * payout_token,
                    "fill_quality": "resolution",
                    "status": "resolved",
                }
            )

    # ---------------------------------------------------------------- equity
    def _equity(self, tick: int) -> float:
        total = self.cash
        for cond, pos in self.positions.items():
            m = self.data.markets[cond]
            p_yes = self.data.price_yes_at(m, tick)
            if p_yes is None:
                p_token = pos.entry_price
            else:
                p_token = p_yes if pos.direction > 0 else 1.0 - p_yes
            total += pos.shares * p_token
        return total

    # ---------------------------------------------------------------- run
    def run(self) -> SimResult:
        tick = self.t_start
        last_day = -1
        while tick <= self.t_end:
            touched = self._refresh_selection(tick)
            for w in self.watched:
                touched |= self._advance_wallet(w, tick)
            self._settle_resolutions(tick)
            self._check_exits(tick)
            k = 1 if self.cfg.signal.type == "single_wallet" else self.cfg.signal.k_min
            for cond in sorted(touched):
                m = self.data.markets.get(cond)
                if m is None:
                    continue
                net, wallets, vwap = self._consensus(cond, tick)
                direction = 1 if net > 0 else -1
                if abs(net) < k:
                    # episode over for both directions
                    self.episode_entered.pop((cond, 1), None)
                    self.episode_entered.pop((cond, -1), None)
                    continue
                # a flip in consensus direction ends the opposite episode
                self.episode_entered.pop((cond, -direction), None)
                key = (cond, direction)
                if self.episode_entered.get(key):
                    continue  # one entry per episode
                if cond in self.positions:
                    self.episode_entered[key] = True
                    continue
                if any(
                    self.data.markets[p.condition_id].event_id == m.event_id
                    for p in self.positions.values()
                ):
                    continue  # event already held: retry when it frees up
                if self._enter(m, direction, wallets, vwap, tick):
                    self.episode_entered[key] = True
            day = tick // DAY_S
            if day != last_day:
                last_day = day
                self.res.equity_daily.append({"ts": tick, "equity": self._equity(tick)})
            tick += TICK_S
        self.res.final_equity = self._equity(self.t_end)
        return self.res
