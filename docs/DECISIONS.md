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
