# STATUS

> Read this first when resuming. Keep it current: what's done, what's next, open blockers.

**Last updated:** 2026-11-15 ~07:30 UTC (session 1)

## Where things stand

- **Phase 0 (bootstrap): DONE** (issue #1). 28/28 endpoint probes pass, fixtures committed, DATA_NOTES filled, CI green on `claude/dreamy-gates-8wlal5`.
- **Phase 1 (data layer): DONE** (issue #2, `reports/phase-1.md`).
  - [x] Gamma catalog DONE: `data/markets.parquet`, 5,648,486 unique markets, resolved payout coverage 100%, resolved_ts 99.997%, payout agreement with chain map 876,332/876,332, fills-condition coverage 99.70% (see DATA_NOTES "Gamma catalog crawl result").
  - [x] `chain_condition_map.parquet`: 1.117M conditions, 992k resolved, token order verified == Gamma.
  - [x] Universe pass 1: 8.24M wallet-months, 2.63M wallets; extraction set per d-013: 164,576 wallets in `data/universe/candidates_extract.parquet`.
  - [x] Pass 2 (fills_norm + prices_5m) REBUILT after d-014: 170.8M owner legs (2.9G), 760k conditions + aggressor tape (1.6G), 56/56 slices.
  - [x] wallet_events (33.4M rows) + wallet_map (163k).
  - [x] Sanity checks: mapping coverage 100.00% (3 sample months); API cross-check 19/20 (d-015); position replay vs user_position median |err| 0.002%.
  - [ ] API backfill deferred by design to Phase 3 Holdout-B time (DECISIONS d-012).
- **Phase 2 (wallet analytics): DONE** (`reports/phase-2.md`).
  - [x] Features cache: 96 Mondays (2024-06-24→2026-04-20) in `data/features/` (asof= + naive= per date) + `data/market_returns/bucket_*.parquet` precompute. Two cgroup OOMs on peak 2026 windows fixed by gate-prefiltered detail aggregates + wallet-hash bucketing (commits 5eb011e, 6a259e0-ish).
  - [x] Validation (Ph2.2): median |realized-PnL error| 0.002% vs chain user_position.
  - [x] Replication (Ph2.4): P(skilled B | skilled A) = 45.1% vs 5.0% base (paper ~44%), 66,400 wallets.
  - [x] Landscape (Ph2.5): 20,510 candidates @2026-02-02; 2.2% skilled; naive∩skill top-20 = 0/20.
- **Phase 3 (backtest): G1 FAILED on Holdout A; Holdout B (final, once) in flight.**
  - Staged search done (~90 configs in `data/experiments.parquet`; B0 bands in `data/baselines.parquet`): k_min=3 consensus is the only positive family; Train winner `skill n40 · fresh_flow k3 · 72h · hold · lat30` = +17.0%/109 trades, B0 excess +18.6pp; flat latency decay 1min→4h; ex-election +24%; widening (N, $-floors) dilutes monotonically (capacity frontier).
  - Validation: V1 +25%/14 trades (excess +52pp, P=0.013). d-017 ≥30-trade bar unmet → redesign r1 (widen funnel, d-018) FAILED on Train (−8.8%).
  - **Holdout A (burned, d-019/d-020): 1 signal, 0 trades in 83 days → G1(a) 0/150 → G1 FAIL.** Mechanism: 2026-era skill score drifts to dust-bet longshot farmers (median $1.9/market) that cannot form $250-level consensus.
  - Redesign r2 (last, d-020): selection gate `min_median_market_cost=50` → Train +8.8%/111, **Validation +41.8%/15 trades, CI_lo +1.5%, B0 excess +51.3pp, P=0.010** → final config for Holdout B (hash c87fe433d0c9).
  - [⏳] Holdout B per d-012: pool frozen (3,695 wallets), `/v2/trades` backfill DONE (37/37 chunks); `scripts/run_holdout_b.py` RUNNING (`scratchpad/holdout_b.log`; features 29/29 cached, prices crawl in flight). Note: `/v2/prices-history` caps spans at 15 days — crawl restricted to the 1,892 signalable conditions (≥k_min distinct watched buyers within one flow window; engine skips missing tapes, non-signalable conditions can't trade) in 14-day chunks.
  - Phase-3 figures in `reports/figures/`; `reports/phase-3.md` DRAFTED (Holdout B placeholders pending).

## Next (in order)

1. Pipeline finishes → fill Holdout B numbers into `reports/phase-3.md`, commit.
2. "Phase 3 result" issue per Operating Rule 10 (G1 FAIL; offer long-horizon paper observation as an option needing Darren's explicit go-ahead).
3. STOP (no paper trader without Darren's explicit say-so).

## Open blockers

- None hard. Disk ~23G free after deleting catalog parts.

## Standing constraints (never stale)

- Paper trading only; no keys, no accounts, no geoblock circumvention (SPEC §0).
- Never tune on holdouts; G1 criteria are §9's, pre-registered.
- Work on branch `claude/dreamy-gates-8wlal5`; main via PR only (d-002).
- Disk ~31GB allowance: stream HF via DuckDB; big months sliced (d-011); extraction capped (d-013).
- Background jobs log to the session scratchpad; all are resumable per-file/per-slice.
