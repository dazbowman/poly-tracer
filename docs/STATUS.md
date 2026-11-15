# STATUS

> Read this first when resuming. Keep it current: what's done, what's next, open blockers.

**Last updated:** 2026-11-15 ~01:10 UTC (session 1)

## Where things stand

- **Phase 0 (bootstrap): DONE.** 28/28 endpoint probes pass, fixtures committed, DATA_NOTES filled, CI green on `claude/dreamy-gates-8wlal5`.
- **Phase 1 (data layer): ~70%, long jobs running.**
  - [x] Gamma catalog crawler (keyset, `closed` both ways). **Full crawl RUNNING in background** (closed-market ids reach ~7M; crawl takes hours). Partial `data/markets.parquet` (56KB, 3-page smoke) will be overwritten when it completes — check `ps aux | grep build_catalog`; relaunch `uv run python scripts/build_catalog.py` if dead (page cache makes re-runs cheap).
  - [x] `chain_condition_map.parquet` BUILT: 1.117M conditions, 992k resolved, token order verified == Gamma. Pass 2 and payouts do NOT wait on the Gamma crawl; the crawl supplies resolved_ts/categories/events for features and backtest.
  - [x] Universe pass 1 DONE: 8.24M wallet-months, 2.63M wallets; loose candidates 532k (96.6% vol); extraction set per d-013: 164,576 wallets (33% vol, 313M legs) in `data/universe/candidates_extract.parquet`.
  - [x] Pass 2 (fills_norm + prices_5m) DONE and REBUILT after the d-014 event-semantics fix: 170.8M owner legs (2.9G) + aggressor tape (1.6G), 56/56 slices.
  - [x] wallet_events (33.4M rows) + wallet_map (163k) DONE.
  - [x] Sanity checks PASS: mapping coverage 100.00% (3 sample months); matched volume ≈ 0.5x public event-summed figures consistently; API cross-check 19/20 (misses are fee-netting, d-015).
  - [⏳] Position validation vs user_position RUNNING (`scratchpad/validate.log`).
  - [ ] `reports/phase-1.md`.
  - [ ] API backfill deferred by design to Phase 3 Holdout-B time (DECISIONS d-012).
- **Phase 2 (wallet analytics): code DONE, data pending.** positions.py (+tests), pnl.py vectorized market returns (+tests), skill.py sign-test/BH/score (+tests), features.py as-of pipeline (+synthetic tests), select.py both methods (+tests). To run: `scripts/build_features.py` (needs catalog + pass 2), then validation vs user_position (§9 Ph2.2), replication check (Ph2.4), landscape report (Ph2.5).
- **Phase 3 (backtest): engine + harness DONE on synthetic, real run pending.** engine.py (11 tests), fills_backtest.py execution model (tests), fees.py (spec worked examples), stats.py event bootstrap (tests), baselines.py B0 (calibrated-world test), **leakage test in CI**, data.py loader, experiments.py registry, run_backtest.py CLI with overrides.

## Next (in order)

1. When pass 2 + catalog finish: run `scripts/build_features.py` (writes `data/features/asof=*.parquet`, ~90 Mondays).
2. Phase 1 sanity checks + `reports/phase-1.md`.
3. Phase 2 validation (50 wallets vs user_position.realizedPnl + Data API), replication check, `reports/phase-2.md`.
4. Smoke backtest (`run_backtest.py --split smoke`), then staged Train search (§8.4: ≤300 configs, selection×N → signal params → exits), Validation shortlist ≤3, Holdout A ONCE. Gate G1.

## Open blockers

- None hard. Watch disk (~28G free; pass-2 outputs est 10-15GB).

## Standing constraints (never stale)

- Paper trading only; no keys, no accounts, no geoblock circumvention (SPEC §0).
- Never tune on holdouts; G1 criteria are §9's, pre-registered.
- Work on branch `claude/dreamy-gates-8wlal5`; main via PR only (d-002).
- Disk ~31GB allowance: stream HF via DuckDB; big months sliced (d-011); extraction capped (d-013).
- Background jobs log to the session scratchpad; all are resumable per-file/per-slice.
