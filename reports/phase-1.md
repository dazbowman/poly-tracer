# Phase 1 — Data layer and catalog

Result: the full 2024-01→2026-04-24 on-chain history is normalized locally (170.8M owner fills for 164,576 candidate wallets + a 5-minute taker tape for 852k+ tokens), validated against the Data API (19/20 fill-level matches) and against chain-computed realized PnL (median error 0.002%). The Gamma catalog (5.65M markets) is complete and agrees with chain payouts on 876,332/876,332 overlapping resolved conditions.
Decision: proceed (Phase 2 analytics on this data).
Key numbers:

- **Universe pass 1:** 8.24M wallet-months, 2.63M distinct wallets, 28 monthly partitions of `order_filled` (2024: 3.9 GB, 2025: 16.4 GB, 2026 Jan–Apr: 58.3 GB).
- **Extraction set (d-013):** 164,576 wallets = necessary conditions of the §7.1 gates (≥$25k volume, ≥30 tokens, ≥30 fills) ∧ cadence caps (≤200 fills/day, ≤100k lifetime). Excluded hyperactive accounts are bot/MM-class by construction and carry ~2/3 of raw volume.
- **fills_norm:** 170,815,862 owner legs, 147k+ wallets, 760,093 distinct conditions, zero invalid prices/sizes, 2.9 GB. **prices_5m:** aggressor-event tape, 76.8M+ bars, 1.6 GB.
- **Gamma catalog:** 5,648,486 unique markets (sports 3.69M, crypto_short 881k, …); resolved markets carry payout 100% / resolution timestamp 99.997%; payout agreement with the chain map 100.00% on the 876k-condition overlap; 99.70% of fills_norm conditions covered (the 0.3% remainder is excluded from returns).
- **Token→market mapping coverage: 100.00%** on three audited months (2024-10, 2025-06, 2026-03) against direct chain counts (spec target ≥99%). The map comes from on-chain `condition.parquet` (1.117M conditions, 992k resolved payouts; token order verified identical to Gamma `clobTokenIds`).
- **API cross-check: 19/20** sampled taker fills found in `/v2/trades` at identical (wallet, ts, token) with size within fee tolerance (API sizes are net of token-side taker fees; ours are gross — d-015).
- **Realized-PnL validation (Phase 2.2, run early):** 50 wallets replayed through the position engine (fills + splits/merges/redemptions) vs chain `user_position.realizedPnl`: **median |error| 0.002%** (target <5%); worst wallet 1.25%; wallets with split/merge share up to 0.32 agree to 1e-10.

Volume plausibility: matched (once-counted) volume by month — 2024-11: $1.42B, mid-2025: ~$0.5–0.7B, 2026-03: $4.98B. These are consistently ≈0.5× public event-summed figures (e.g. the widely-cited ~$2.5B for 2024-11), as expected since each match emits both maker-side and taker-side events; the ratio is stable across 28 months, which is itself a consistency check.

What could make this wrong:

- **CTF event semantics** were initially misread (maker+taker leg expansion double-counted aggressors and booked phantom complement-token trades on mint/merge matches). Caught by the API cross-check, fixed in d-014, outputs fully rebuilt. The validation numbers above are from the rebuilt data.
- **Token-side taker fees are not netted** (d-015): leader share balances on fee-enabled 2026 markets are overstated by ~1–5% on buys. Realized-PnL validation shows the aggregate effect is negligible (the subgraph books the same convention), but per-position open sizes in 2026 carry that bias.
- Pass-1 wallet-month aggregates still use the old both-legs counting (thresholds there are generous; extraction is a superset — exact numbers come from fills_norm).
- The dataset ends 2026-04-24 (plus a missing v1 tail to block 86,126,998); Holdout B is API-sourced and scoped per d-012.
- 64 conditions have scalar (non-0/0.5/1) payouts — FPMM-era artifacts, excluded from backtests via the catalog join.

Next:

- Weekly features cache (`scripts/build_features.py`) → Phase 2 landscape + Phase 3 backtest.
- Phase 1.5 API backfill intentionally deferred to Holdout-B time (d-012).
