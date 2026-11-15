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
- **Phase 2 (wallet analytics): code DONE (validation §9 Ph2.2 already PASSED), features cache building.**
  - [⏳] `scripts/build_features.py` RUNNING detached (log: `scratchpad/features.log`): one-time `data/market_returns/bucket_*.parquet` precompute, then ~96 Mondays of `data/features/asof=*.parquet` + `naive=*.parquet`. Resumable per-file; just rerun the same command if it dies.
  - Then: replication check (Ph2.4), landscape report (Ph2.5) → `reports/phase-2.md`, issue.
- **Phase 3 (backtest): engine + harness DONE on synthetic, real run pending.** engine.py (11 tests), fills_backtest.py execution model (tests), fees.py (spec worked examples), stats.py event bootstrap (tests), baselines.py B0 (calibrated-world test), **leakage test in CI**, data.py loader, experiments.py registry, run_backtest.py CLI with overrides.

## Next (in order)

1. Features build finishes → spot-check a few asof dates (candidate counts, label mix, score distribution).
2. `scripts/replication_check.py`, `scripts/phase2_report.py` → `reports/phase-2.md` + "Phase 2 complete" issue.
3. Smoke backtest (`run_backtest.py --split smoke`), then staged Train search (§8.4: ≤300 configs, selection×N → signal params → exits), Validation shortlist ≤3, Holdout A ONCE. Gate G1.

## Open blockers

- None hard. Disk ~23G free after deleting catalog parts.

## Standing constraints (never stale)

- Paper trading only; no keys, no accounts, no geoblock circumvention (SPEC §0).
- Never tune on holdouts; G1 criteria are §9's, pre-registered.
- Work on branch `claude/dreamy-gates-8wlal5`; main via PR only (d-002).
- Disk ~31GB allowance: stream HF via DuckDB; big months sliced (d-011); extraction capped (d-013).
- Background jobs log to the session scratchpad; all are resumable per-file/per-slice.
