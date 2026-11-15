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
- **Phase 3 (backtest): engine + harness DONE on synthetic, real run pending.** engine.py (11 tests), fills_backtest.py execution model (tests), fees.py (spec worked examples), stats.py event bootstrap (tests), baselines.py B0 (calibrated-world test), **leakage test in CI**, data.py loader, experiments.py registry, run_backtest.py CLI with overrides.

## Next (in order)

1. Smoke backtest (`run_backtest.py --split smoke`) — first real-data engine run; expect loader/perf issues to shake out.
2. Staged Train search (§8.4: ≤300 configs, selection×N → signal params → exits), with/without Oct–Nov 2024 reporting.
3. Validation shortlist ≤3, Holdout A ONCE, Holdout B per d-012 (needs API backfill first). Gate G1 + issue.

## Open blockers

- None hard. Disk ~23G free after deleting catalog parts.

## Standing constraints (never stale)

- Paper trading only; no keys, no accounts, no geoblock circumvention (SPEC §0).
- Never tune on holdouts; G1 criteria are §9's, pre-registered.
- Work on branch `claude/dreamy-gates-8wlal5`; main via PR only (d-002).
- Disk ~31GB allowance: stream HF via DuckDB; big months sliced (d-011); extraction capped (d-013).
- Background jobs log to the session scratchpad; all are resumable per-file/per-slice.
