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

**d-017 · 2026-11-15 · Validation shortlist (frozen before any Validation run)**

- Train-stage results (full table in `data/experiments.parquet`, §8.4 stages 1–3): consensus depth k_min=3 is the only sign-flipping lever; single-wallet and k=2 variants are cost-dragged noise. Best family: skill-selected N=40, fresh_flow k=3, 72h window, hold-to-resolution — Train ROI +17.0% (109 trades), B0 excess +18.6% (P(B0≥strat)=0.022), ex-Oct/Nov-2024 ROI +24%, flat latency-decay curve (1min→4h), profits spread over politics/crypto/sports (geopolitics negative).
- Shortlist for ONE pass over Validation (2025-10-01→2026-01-31), ≤3 per SPEC §8.4:
  - V1 `skill n40 · fresh_flow k3 · 72h · hold · lat30` (Train winner)
  - V2 `skill n20 · fresh_flow k3 · 72h · hold · lat30` (N-robustness of the same family)
  - V3 `naive_pnl n10 · holdings k3 · conviction 2.0 · hold · lat30` (the owner's original leaderboard+holdings idea, best Train representative: +13.3%, 48 trades)
- Decision rule (pre-stated): the config taken to Holdout A is the shortlist member with the highest Validation B0-excess among those with Validation ROI > B0 mean and ≥30 resolved trades; if none qualifies, redesign round 1 (Train/Val only) per the §0 stop rule. Latency stays 30min; no further Train tuning after this entry.
- Multiple-comparisons caveat recorded: the Train winner is the best of ~72 configs; Train p-values are selection-biased by construction. Validation is the first clean read.

**d-018 · 2026-11-15 · Redesign round 1 (of ≤2): widen the consensus funnel**

- Validation verdict under d-017's rule: all three shortlist configs positive with large B0 excess (V1 +52.4% excess, P(B0≥strat)=0.013; V3 +34.3%, P=0.087) but 14/1/10 resolved trades — none reaches the ≥30-trade qualifier, and ~3.5 trades/month would also mechanically fail G2's ≥100-resolved-trades requirement in Phase 4. The failure mode is signal scarcity, not sign.
- Redesign (pre-registered BEFORE any run; Train/Validation only): keep `fresh_flow k_min=3, 72h window, hold, lat30` and widen the funnel —
  `selection.n_wallets 40→80`, `signal.min_wallet_add_usd 250→100`, `signal.min_wallet_position_usd 500→250`.
  Rationale: k=3 consensus depth is what separates positive from negative ROI on Train; N and the per-wallet notional floors only control how often 3 watched wallets can overlap. No category filters, no new signal logic, no latency change.
- Decision rule (unchanged from d-017): run the redesigned config on Train (sanity), then ONCE on Validation; qualify iff Validation ROI > B0 mean, B0 excess > 0, and ≥30 resolved trades. Qualify → Holdout A once with it. Fail → redesign round 2 or the §0 stop rule (negative-result report).

**d-019 · 2026-11-15 · Redesign round 2 declined; Holdout A executed once with the frozen V1 config**

- Round-1 decomposition on Train (base k3/w72/hold/lat30): N=40 → +17.0% (109 trades); N=60 → +3.5% (174); N=80 → +1.9% (213); N=40+halved floors → −3.2% (159); N=80+halved floors → −8.8% (297). Monotone dilution in both widening levers: the edge is concentrated in top-40-scored wallets' ≥$250 adds.
- G1(a) needs ≥150 resolved trades on Holdout A (<3 months); the edge-bearing config runs at 3.5–7 trades/month → ~10–20 holdout trades. Reaching G1(a) requires ~7× the widening that already destroyed the edge at 2.7×. No §8.4-space mechanism (per-category selection, exits, chase — swept or decomposed) plausibly multiplies deep-consensus frequency ~10× without the same dilution, so a second redesign round would be overfitting ritual, not research. Round 2 is declined.
- Pre-stated execution: Holdout A (2026-02-01→2026-04-24) is run ONCE with V1 exactly as frozen in d-017 (skill n40 · fresh_flow k3 · 72h · hold · lat30), plus its B0 band, and the full G1 checklist is reported item by item. Expected verdict: G1 FAILS on (a) regardless of ROI; (b)–(g) reported for the record. Holdout B follows per d-012's scope as the separate, weaker continuation check.

**d-020 · 2026-11-15 · G1 FAILED on Holdout A (0 trades); redesign round 2: copyability gate**

- Holdout A, run once with frozen V1 (d-019): **1 signal, 0 fills in 83 days** → G1(a) fails at 0/150; G1 verdict: **FAIL** (A is burned; G1 as pre-registered can never pass).
- Mechanism (diagnosed from the A run + features): the skill score drifts in the 2026 regime to longshot/dust-bet wallets — 2026-03 top-40: median cost-weighted ROI 2.30, median per-market cost **$1.90**, sports/esports-heavy, zero 3-wallet overlaps at the $250 add level; 2025-11 top-40: median $117/market, overlaps present. Selection chooses wallets whose portfolios cannot mechanically form copyable consensus.
- Redesign round 2 (last; spec §9 allows ≤2 using Train/Validation only): add ONE selection gate — skilled wallets must also have `median_market_cost ≥ X`, X ∈ {50, 100, 250} swept on **Train only**; best X by Train ROI with ≥trade-count sanity, confirmed ONCE on Validation (qualifier: ROI > B0 mean, excess > 0; trade count reported — the d-017 ≥30 bar is dropped as unreachable at this strategy's structural frequency and is superseded by this entry). The Holdout-A period (2026-02→04) is NOT used for tuning.
- Final judgment: Holdout B once (d-012 scope, 2026-04-25→present, API-sourced, weaker execution model, reported separately). Whatever B shows, `reports/phase-3.md` records G1 FAIL as the headline, with the low-frequency baseline-beating pattern (Train +17%/109, Val +25%/14, excesses +19/+52pp) and the copyability-drift finding as the research content. Per Operating Rule 10, no paper trader unless Darren explicitly asks.
