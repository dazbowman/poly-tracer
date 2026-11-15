"""Leakage test (SPEC §9 Phase 3.2, must pass in CI).

Build a rich synthetic world, run the engine; then corrupt EVERYTHING after a
cut time — prices randomized, payouts flipped for markets resolving after the
cut, fills scrambled, future selections shuffled — and run again. Every logged
decision at or before the cut must be byte-identical. If any future data leaks
into decisions, this fails.
"""

import numpy as np

from polycopy.backtest.engine import Engine, SimData, WalletFills
from polycopy.sim.fills_backtest import TokenTape
from synthworld import T0, H, base_cfg, make_data, market

T_CUT = T0 + 12 * 24 * H
T_END = T0 + 25 * 24 * H


def build_world(seed=5):
    rng = np.random.default_rng(seed)
    markets = []
    fills = {f"w{i}": None for i in range(4)}
    buys = {w: [] for w in fills}
    for j in range(14):
        resolved_h = 24 * (6 + j * 1.5)
        m = market(
            cond=f"c{j}",
            event=f"e{j}",
            end_h=resolved_h - 2,
            resolved_h=resolved_h,
            payout=float(rng.integers(0, 2)),
        )
        markets.append(m)
        # staggered wallet entries across the sim
        for i, w in enumerate(fills):
            t = T0 + int(j * 1.3 * 24 * H) + (i + 1) * H
            if rng.random() < 0.8:
                buys[w].append((f"c{j}", t, 0.55 + 0.05 * (j % 3), 600 + 50 * i))
    wf = {}
    for w, rows in buys.items():
        rows.sort(key=lambda r: r[1])
        wf[w] = WalletFills(
            ts=np.array([r[1] for r in rows], dtype=np.int64),
            condition_id=[r[0] for r in rows],
            is_yes=np.full(len(rows), True),
            bought=np.full(len(rows), True),
            shares=np.array([r[3] / r[2] for r in rows]),
            usdc=np.array([float(r[3]) for r in rows]),
        )
    sels = {
        T0: ["w0", "w1"],
        T0 + 7 * 24 * H: ["w0", "w1", "w2"],
        T0 + 14 * 24 * H: ["w1", "w2", "w3"],
        T0 + 21 * 24 * H: ["w0", "w2", "w3"],
    }
    return make_data(markets, wf, selections=sels, price=0.58)


def corrupt_after(data: SimData, t_cut: int, seed=99) -> SimData:
    rng = np.random.default_rng(seed)
    tapes = {}
    for tok, tp in data.tapes.items():
        mask = tp.bucket_ts > t_cut
        lp = tp.last_price.copy()
        tb = tp.taker_buy_vwap.copy()
        tsv = tp.taker_sell_vwap.copy()
        vol = tp.volume_usdc.copy()
        tbu = tp.taker_buy_usdc.copy()
        n = int(mask.sum())
        lp[mask] = rng.uniform(0.01, 0.99, n)
        tb[mask] = rng.uniform(0.01, 0.99, n)
        tsv[mask] = rng.uniform(0.01, 0.99, n)
        vol[mask] = rng.uniform(0, 5_000, n)
        tbu[mask] = vol[mask] * rng.uniform(0, 1, n)
        tapes[tok] = TokenTape(
            bucket_ts=tp.bucket_ts,
            taker_buy_vwap=tb,
            taker_sell_vwap=tsv,
            taker_buy_usdc=tbu,
            volume_usdc=vol,
            last_price=lp,
        )
    markets = {}
    for cid, m in data.markets.items():
        if m.resolved_ts and m.resolved_ts > t_cut:
            m = type(m)(
                condition_id=m.condition_id,
                event_id=m.event_id,
                yes_token=m.yes_token,
                no_token=m.no_token,
                category=m.category,
                neg_risk=m.neg_risk,
                end_ts=m.end_ts,
                resolved_ts=m.resolved_ts + int(rng.integers(0, 48)) * H,
                payout_yes=1.0 - m.payout_yes,  # flip future outcomes
            )
        markets[cid] = m
    fills = {}
    for w, wf in data.fills.items():
        mask = wf.ts > t_cut
        usdc = wf.usdc.copy()
        shares = wf.shares.copy()
        bought = wf.bought.copy()
        usdc[mask] = usdc[mask] * rng.uniform(0.1, 3.0, int(mask.sum()))
        shares[mask] = shares[mask] * rng.uniform(0.1, 3.0, int(mask.sum()))
        bought[mask] = rng.random(int(mask.sum())) < 0.5
        fills[w] = WalletFills(
            ts=wf.ts,
            condition_id=wf.condition_id,
            is_yes=wf.is_yes,
            bought=bought,
            shares=shares,
            usdc=usdc,
        )
    selections = {
        k: (v if k <= t_cut else list(rng.permutation([*v, "w9"])))
        for k, v in data.selections.items()
    }
    return SimData(
        markets=markets,
        tapes=tapes,
        fills=fills,
        selections=selections,
        wallet_median_cost=data.wallet_median_cost,
    )


def decisions_up_to(log: list[str], t_cut: int) -> list[str]:
    out = []
    for line in log:
        parts = line.split()
        if int(parts[1]) <= t_cut:
            out.append(line)
    return out


def _run(data) -> list[str]:
    cfg = base_cfg(k_min=2)
    eng = Engine(cfg, data, t_start=T0, t_end=T_END)
    return eng.run().decision_log


def test_decisions_before_cut_identical_under_future_corruption():
    clean = build_world()
    corrupted = corrupt_after(build_world(), T_CUT)
    log_a = decisions_up_to(_run(clean), T_CUT)
    log_b = decisions_up_to(_run(corrupted), T_CUT)
    assert log_a == log_b
    assert log_a, "leakage test vacuous: no decisions before the cut"
    # sanity: the corruption really does change what happens later
    full_a = _run(clean)
    full_b = _run(corrupted)
    assert full_a != full_b, "corruption had no effect at all — test not probing anything"


def test_fills_decided_before_cut_identical():
    """Orders whose execution window closes before the cut must fill identically."""
    cfg = base_cfg(k_min=2)
    window_s = (cfg.execution.latency_minutes + cfg.execution.backtest_window_minutes) * 60
    clean = Engine(cfg, build_world(), t_start=T0, t_end=T_END).run()
    corr = Engine(cfg, corrupt_after(build_world(), T_CUT), t_start=T0, t_end=T_END).run()

    def fills_before(res):
        return [
            (t["signal_id"], t["ts_exec"], round(t.get("avg_price", -1), 10), t["status"])
            for t in res.trades
            if t["side"] == "buy" and t["ts_exec"] + window_s <= T_CUT
        ]

    assert fills_before(clean) == fills_before(corr)
    assert fills_before(clean)
