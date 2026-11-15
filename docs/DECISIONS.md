# DECISIONS

One entry per non-obvious choice: date, decision, alternatives, reason. Newest last.

---

**d-001 · 2026-11-15 · Python 3.12 pinned via uv; local interpreter is 3.13**

- Decision: `requires-python = ">=3.12"`, `.python-version = 3.12` so uv provisions 3.12 everywhere (CI and any machine), matching the spec's stack.
- Alternatives: run on the container's 3.13.
- Reason: reproducibility across sessions/CI beats saving one interpreter download; spec names 3.12.

**d-002 · 2026-11-15 · CI triggers on push to `main` and `claude/**`, plus PRs to `main`**

- Decision: deviate slightly from spec §9 Phase 0 ("CI that runs only on push/PR to `main`").
- Alternatives: literal spec (CI only on main).
- Reason: the hosting setup gives this project a dedicated dev branch (`claude/dreamy-gates-8wlal5`) and direct pushes to `main` are not permitted, so with the literal rule CI would never run during development and "keep main green" would be unverifiable. The intent of the rule is (a) lint+tests gate everything landing on main and (b) don't waste Actions minutes; a single ~1-minute job on pushed batches keeps both.

**d-003 · 2026-11-15 · Spec committed verbatim including one paste artifact**

- Decision: `docs/SPEC.md` §9 Phase 4 contains `reports/weekly/YYYY-[Www.md](https://Www.md)`, a markdown auto-link artifact from the original document. Preserved as received; interpreted as `reports/weekly/YYYY-Www.md` (ISO week numbering).
- Reason: spec says "commit verbatim"; the interpretation is recorded here instead of editing the source of truth.

**d-004 · 2026-11-15 · Config models are frozen pydantic with `extra="forbid"`**

- Decision: every config key is typed; unknown keys raise. Experiment identity = SHA-256 of the resolved config JSON (12 hex chars).
- Reason: the experiment registry (spec §8.4) hinges on config hashes meaning something; a silently-ignored typo in a sweep YAML would corrupt the registry.

**d-005 · 2026-11-15 · Skill score: shrink the whole interval**

- Decision: score = κ·(m − 1.64·se), κ = n/(n+n0), m = cost-weighted mean edge, se = randomization SE.
- Alternatives: shrink only the mean (m·κ − 1.64·se), or shrink se separately.
- Reason: the spec's "LCB of the shrunk mean" is ambiguous; shrinking the interval is monotone in n, simple, and conservative for small n. Pinned by unit tests.

**d-006 · 2026-11-15 · Two-sided share approximated by UTC-day buckets**

- Decision: per (wallet, token, UTC day): two-sided notional = 2·min(buy$, sell$); share = Σ over days ÷ total notional.
- Alternatives: exact 24h sliding windows (heavy at 100M+ rows).
- Reason: day-bucketing misses same-token round trips straddling midnight (undercount ≤ 2×) but the 0.30 threshold has wide margin for real MMs, who churn constantly.

**d-007 · 2026-11-15 · naive_pnl marks = last traded price**

- Decision: leaderboard-style window PnL marks positions at the last traded price ≤ t (prices_5m); unseen tokens mark at 0.5.
- Alternatives: book midpoints (not in historical data), Data API user-pnl series (not point-in-time safe).
- Reason: no historical books exist; last-trade is the only honest as-of mark. Replicates ranking, not cents.

**d-008 · 2026-11-15 · Backtest chase cap acts as a limit price on the fill**

- Decision: §7.7 "slippage capped at the chase limit" implemented as: executed price = min(model price, signalers' VWAP + chase_limit), full fill at the cap.
- Alternatives: reject the trade when the model price exceeds the cap; partial fills.
- Reason: a live copier would place a limit order at the cap, so capping mimics intent; slightly optimistic on fill probability, which the paper phase (real books) cross-checks. Guards already reject entries whose last price exceeds the cap.

**d-009 · 2026-11-15 · S2 chase-guard reference = VWAP of window adds; S1 = avg cost**

- Decision: per spec §7.4, fresh-flow signals compare entry to the VWAP of signalers' adds inside the flow window (tracked per fill); holdings signals use their average cost of the current position.

**d-010 · 2026-11-15 · Exits pay the same latency as entries**

- Decision: E2 follow-exit sells execute at tick + latency_minutes with the sell-side tape model. Spec specifies latency for entries only; symmetric latency is the honest default.

**d-011 · 2026-11-15 · Universe pass big-month slicing**

- Decision: 2026 monthly files are aggregated in 8 timestamp slices using raw-string bounds (10-digit unix strings compare lexicographically = numerically), so DuckDB zone maps prune row groups; per-(wallet,month) outputs merge across slices with n_tokens as an upper bound (sum of per-slice distincts).
- Reason: exact single-pass grouped aggregation spilled >18 GB and filled the disk allowance twice. n_tokens is only used for generous candidate thresholds, never for §7.1 gates.

**d-012 · 2026-11-15 · Holdout B scope (planned)**

- Decision (to implement in Phase 3): Holdout B (2026-04-25→now) freezes the candidate pool to wallets known at 2026-04-24; API backfill (/v2/trades) only for the top ~3,000 candidates by as-of score/volume plus every wallet any shortlisted config ever selected; prices from /v2/prices-history at 5-min buckets; execution falls back to last-price+1.5¢ mode when taker-VWAP is unavailable.
- Reason: full-universe backfill would need ~10^5–10^6 API requests. The spec already labels Holdout B "weaker; report separately"; a frozen candidate pool keeps it honest (point-in-time, same selection algorithm) at feasible cost. The §9 Phase 4 parity check still validates API-vs-dataset selection overlap.

**d-013 · 2026-11-15 · Pass-2 extraction set: gate-necessary conditions + cadence caps**

- Decision: fills_norm extracts only wallets satisfying necessary conditions of the §7.1 gates (lifetime volume ≥ $25k, distinct-token upper bound ≥ 30, fills ≥ 30) AND cadence caps (≤ 200 fills/day lifetime average, ≤ 100k lifetime fills) → 164,576 wallets, ~313M fill-legs, 33% of global volume. The loose pass-1 candidate file (532k wallets, 96.6% of volume) is kept for reporting. Wallets above the cadence caps are auto-labeled hyperactive (bot/MM class) from pass-1 aggregates and reported as such in the Phase-2 landscape without per-fill features.
- Alternatives: extract everything (~2B legs ≈ 30–50 GB, exceeds the disk allowance); tighter caps (fpd ≤ 100 risks cutting bursty sports bettors).
- Reason: the cut volume is exactly the §7.3-excluded churn (volume/PnL > 200, sub-10s cadence). A >200-fills/day account is not a copyable signal source for a 30-minute-latency taker strategy; any "edge" there is latency/liquidity-provision edge by construction. prices_5m still aggregates ALL fills.

**d-014 · 2026-11-15 · OrderFilled legs: owner-only; prices tape from aggressor events**

- Decision: fills_norm takes exactly one leg per OrderFilled event (owner = `maker` field; `bought` iff makerAssetId='0'; role 'taker' iff the event's taker field is an exchange contract). prices_5m aggregates aggressor events only (one per match, on the aggressor's token — the /v2/trades-equivalent tape). tx hash and counterparty columns dropped for size; API cross-checks match on (wallet, ts, token, size).
- Found by: sanity check 3 — 0/20 API matches exposed that naive maker+taker leg expansion double-counts aggressors, books phantom complement-token trades on mint/merge matches, and treats the exchange contracts as wallets.
- Impact: pass-1 wallet-month volumes are inflated for takers (≤2x) and include the exchange contracts; thresholds there are generous and the extraction set is a superset, so no pass-1 rebuild — features compute exact numbers from the corrected fills_norm. Pass-2 outputs fully rebuilt.

**d-015 · 2026-11-15 · Gross fill sizes kept; token-side taker fees not netted**

- Decision: fills_norm stores gross matched size (price = gross price, which is the correct tape price). On fee-enabled markets (mostly 2026 sports/crypto) the Data API shows taker sizes net of a token-side fee (observed 1–5%); the chain `fee` column's units did not decode cleanly against observed deductions and a 27GB fee_refunded table further muddies net amounts.
- Impact: leader-wallet share balances slightly overstated on fee-era buys; bot economics unaffected (we model PM-US fees explicitly). The user_position validation measures the aggregate effect; revisit with exact fee decoding only if it breaches the 5% target. API cross-check tolerance widened to 6% accordingly.

**d-016 · 2026-11-15 · B0 band: R matched draws per trade, compared on the matched subset**

- Decision: the §8.2 baseline is computed per saved run as R (default 400) replications; replication r buys, for every matched strategy trade, that trade's r-th sampled opportunity from the (category, TTR-bucket, entry-week, price±2¢) pool at taker-VWAP+1¢ with PM-US fees, sized to the trade's notional. The band is the 2.5–97.5 percentile of the R portfolio ROIs; the strategy ROI it is compared against is recomputed on the matched trades only (unmatched trades, typically <2%, are reported). Pool cells are hourly aggregates of prices_5m with ≥$100 taker-buy flow, resolved markets only. Registry: `data/baselines.parquet` (scripts/run_baseline.py).
- Alternatives: comparing against the all-trade strategy ROI mixes portfolios when matches are missing; per-trade mean-pnl averaging understates the band's width (it averages away the variance a single matched portfolio actually has).
- First measurement (Train, default config = skill n20 fresh_flow k2): strategy −5.5% vs B0 mean −6.1%, band (−23%, +15%), excess +0.6%, P(B0 ≥ strategy)=0.48 → the default copy signal shows no alpha over matched random taker buys; its loss is the cost drag.
