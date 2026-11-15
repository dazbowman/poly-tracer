# PolyCopy: Build Spec for the Prediction-Market Copy-Trading Research Bot

> **For the agent reading this:** you own this project from start to finish. Darren (the owner) wants as little involvement as possible. Make every decision yourself, write down why in `docs/DECISIONS.md`, and only contact him for the short list in §13. This document is the source of truth. Commit it to the repo verbatim as `docs/SPEC.md` in your first commit.
>
> **Resuming in a later session:** read `docs/STATUS.md` first. It must always say what's done, what's next, and any open blockers, so a fresh session can continue with no other context.

---

## 0. Operating rules (read before doing anything)

1. **Paper trading only.** Through Phase 4, nothing in this repo may place, sign, or route a real order on any venue. Don't create or ask for API keys, private keys, or account credentials. Everything here uses public, unauthenticated data.
2. **No geoblock circumvention.** Darren is in Ohio, USA. Never place or simulate placing orders *on* international Polymarket (polymarket.com) as if he could trade there, and never use VPNs or proxies to get around a geoblock. Reading its public market and wallet data is fine and is the whole point. If a read endpoint answers with a geoblock or a 403, record it in `docs/DATA_NOTES.md` and use a different source.
3. **Kalshi: official public API only.** Don't scrape kalshi.com's web-client or "Ideas" profile JSON. It's undocumented and a likely ToS problem.
4. **Be polite to APIs.** Keep total requests to each host at or below about 5 per second. Use exponential backoff and honor `Retry-After` on 429 and 503. Send a descriptive User-Agent (`polycopy-research/0.x (+github repo url)`). Cache everything you can.
5. **No paid data and no new accounts** without asking Darren (§13). Free, keyless sources only.
6. **Don't fool yourself.** The default expectation is that this strategy does *not* beat the market after fees (see §2). Your job is to find out honestly. Pre-register the go/no-go criteria (§9) before looking at holdout data, and never tune on the holdout. A clean negative result is a successful outcome for this project.
7. **Use only data that existed at the time.** Every backtest decision at time *t* may use only data timestamped before *t*. Wallet skill at *t* may use only markets that had resolved before *t*. Write a test that enforces this (§9, Phase 3).
8. **Keep docs current as you go:** `docs/STATUS.md` (always), `docs/DECISIONS.md` (one entry per non-obvious choice: date, decision, alternatives, reason), `docs/DATA_NOTES.md` (endpoint quirks and schemas), and the phase reports under `reports/`.
9. **Commit small and often.** Use conventional commit messages. Keep `main` green (lint and tests pass).
10. **Stop rule:** if Gate G1 (§9) fails after the allowed redesign rounds, write the negative-result report, update STATUS, open a "Phase 3 result" issue (§11), and stop. Don't deploy the paper trader for a strategy that failed G1 unless Darren explicitly says to.

---

## 1. Goal and hypothesis

**Darren's original idea:** take the most profitable traders on the prediction-market leaderboards, keep a small set of them (5–20, to be tuned), and when several of them hold the same position in the same direction, copy it.

**Research question:** Does following the positions or new trades of selected Polymarket wallets, using only information available at the time and after realistic latency, spread, slippage and fees, earn a positive expected return that beats a market-price baseline?

**Final deliverables:**
1. A reproducible backtest that answers the question for several variants, including Darren's original "top-PnL leaderboard plus holdings consensus" version.
2. If the backtest passes G1: a live paper trader on GitHub Actions that simulates fills against the order book of a venue Darren can legally use (Polymarket US first, Kalshi second) and produces weekly reports.
3. Clear written reports a non-specialist can skim in five minutes.

**Out of scope:** real-money trading, market making, cross-venue arbitrage, Kalshi wallet copying (Kalshi trades aren't public), UI and web dashboards (markdown reports and PNG charts are enough), and LLM calls at runtime.

---

## 2. What we already know

This is research done on 2026-11-14 so you don't repeat it. Re-verify anything that affects money math.

### 2.1 Venue facts
- **Polymarket (global, polymarket.com):** settles on Polygon, so every wallet's trades and positions are public. The Data API v2 is public with no auth. **This is the signal source.**
- **Polymarket US (polymarket.us):** a separate CFTC-regulated exchange with its own markets, accounts and API. Nothing on it settles on Polygon, so its trades aren't attributable. **This is Darren's account and the primary paper-execution venue.** Its public gateway needs no auth.
- **Kalshi:** CFTC-regulated, and individual trades aren't public. The leaderboard is opt-in (off by default). **Use it only as a secondary execution venue for pricing, not as a signal source.**
- International Polymarket is geoblocked for US IPs. Darren cannot trade there.

### 2.2 Fees (copying is a *taker* strategy, so fees matter a lot)
- **Polymarket US, effective 2026-11-14:** taker fee = `Θ × C × p × (1 − p)`, with Θ set by trailing 30-day taker volume:

  | Tier | Volume | Θ |
  |---|---|---|
  | T1 | < $10k | 0.095 |
  | T2 | $10k–<$50k | 0.085 |
  | T3 | $50k–<$250k | 0.075 |
  | T4 | ≥ $250k | 0.0695 |

  New accounts pay 0.0695 for days 1–7. Fees round to the nearest $0.01 per order using banker's rounding. There are no category rates. Makers pay nothing; rebates only start at ≥ $10M maker volume. Source: `https://docs.polymarket.us/fees.md`. **Darren would be Tier 1, so use Θ = 0.095.**
- **Kalshi:** taker fee = `0.07 × C × p × (1 − p)`, rounded up to the cent (verify the current schedule and rounding at `docs.kalshi.com`).
- **Polymarket global:** taker fee = `C × feeRate × p × (1 − p)`, where the rate depends on category (geopolitics is 0). Get the live rate per token from `https://clob.polymarket.com/fee-rate?token_id=…`.
- **Worked examples** (Darren's T1 rate vs Kalshi):
  - At p = 0.50, 100 contracts: PM US ≈ $2.38 (4.75% of cost); Kalshi $1.75 (3.5%).
  - Buying at 0.855: PM US fee ≈ 1.18¢ per contract, which is 8.1% of the 14.5¢ maximum profit (Kalshi ≈ 0.87¢, or 6.0%). Break-even win probability ≈ 0.867.
  - In general, a copied buy at price p needs average (outcome − p) greater than Θ·p(1−p) + half-spread + slippage.

### 2.3 Academic evidence
- **Gómez-Cram, Guo, Jensen, Kung (2026), "Prediction Market Accuracy: Crowd Wisdom or Informed Minority?"** Covers every Polymarket trade from 2023–2025: 1.72M accounts and 98,906 events.
  - Only **3.14%** of accounts are "skilled" by a sign-randomization test.
  - Only **12%** of the top earners overlap with the skilled set, and about **60%** of "lucky winners" reverted to losses out of sample.
  - **44%** of skilled accounts stayed skilled out of sample (vs about 10% for mutual funds).
  - Skilled accounts traded about **79 markets** each on average.
  - A 1-percentage-point increase in skilled net buying raised the probability of the correct outcome by about **8 bps**: real, but small.
  - Coverage: theblock.co/post/398902, gncrypto.news (search the title).
  - **Takeaway:** don't select wallets by raw PnL. Use a luck-adjusted skill test.
- **"Who wins and who loses in prediction markets? Evidence from Polymarket" (SSRN).** 588M trades.
  - The top 1% capture **76.5%** of profits.
  - Winners mostly *provide* liquidity with limit orders, while losers take liquidity with market orders.
  - Monthly performance is only weakly persistent.
  - The most successful users traded sports frequently, often on different teams.
  - **Takeaway:** many top wallets are market makers whose positions are inventory, not opinions. Filter them out.
- **Reichenbach & Walther (SSRN 5910522).**
  - Fewer than a third of traders are profitable.
  - Some skill is persistent.
  - Prices are well calibrated overall; inaccuracies cluster early in a market's life and near resolution.
- **Calibration studies** (e.g., Kalshi CS2 pre-match prices) find prices close to fair. **The null hypothesis is "the price is right."**

### 2.4 Live data observations (Data API v2, monthly board, 2026-11-14)
- **#1 and #2 by monthly PnL** (+$3.65M and +$2.6M) are soccer whales making single bets of $100k–$450k. #2's five remaining positions were all resolved losers totaling −$1.34M. These are high-variance, single-category wallets.
- **#4** mostly buys "No" at 80–97¢ on Fed and AI-valuation markets: harvesting near-certainties.
- **#3** is diversified, with dozens of $500–$2.6k positions across sports, crypto, politics and macro.
- **Consensus example.**
  - The trade: #3 and #4 both held "Yes, Fed hikes 25 bps at the Dec 2026 meeting," entered at 20¢ and 75.5¢. The market was at 85.5¢.
  - What a copier gets: entry at about 85.5¢, which needs > ~86.7% true probability after fees.
  - Why it matters: **their edge was the entry price, which a copier doesn't get.** The chase guard (§7.4) exists for this.
- **#20 on the monthly board** traded about 2,560 shares in the month yet showed +$616k. Finite-window PnL includes repricing of old positions.
- **Several wallets show 100M+ shares of volume against about $1M PnL.** These are almost certainly market makers or bots.

---

## 3. Data sources (verify each in Phase 0; record findings in `docs/DATA_NOTES.md`)

| Source | Base URL | Auth | Use | Notes |
|---|---|---|---|---|
| PM Data API v2 | `https://data-api.polymarket.com/v2/…` | none | leaderboard, wallet positions, trades, activity, holders, price history | OpenAPI spec at `/v2/openapi.json`. The response envelope is `{data, pagination{limit,offset,has_more,next_cursor}}`; page with the cursor only. **The v1 leaderboard returned a client error on 2026-11-14; use v2.** |
| PM Gamma API | `https://gamma-api.polymarket.com` | none | market and event catalog, token IDs, tags, resolution | Spec at `https://docs.polymarket.com/api-spec/gamma-openapi.yaml` |
| PM CLOB (read) | `https://clob.polymarket.com` | none for reads | order books, prices history, fee rate | Spec at `https://docs.polymarket.com/api-spec/clob-openapi.yaml`. Never use the trading endpoints. |
| PM WebSocket | `ws-live-v2.polymarket.com` | none | real-time market feed (optional) | AsyncAPI spec at `https://ws-live-v2.polymarket.com/asyncapi.json` |
| PM docs index | `https://docs.polymarket.com/llms.txt` and `https://docs.polymarket.com/_llms/en/api-reference/predictions.txt` | n/a | endpoint discovery | |
| Historical fills (HF) | `hf://datasets/moose-code/polymarket-onchain-v1` | none | the full on-chain history for backtests | See §3.1 |
| Polymarket US gateway | `https://gateway.polymarket.us` | none for public reads | US market catalog and order books for paper fills | `GET /v1/markets` (filters: active, closed, categories, endDate…), `GET /v1/markets/{slug}/book` (`bids`/`offers`, `px.value` decimal strings), plus settlement and price-history endpoints. Docs index: `https://docs.polymarket.us/llms.txt`. **There is no public REST trades endpoint and no field linking a US market to a global one.** |
| Kalshi | `https://api.elections.kalshi.com/trade-api/v2` | none for public market data | secondary paper venue | `/markets`, `/events`, `/markets/{ticker}/orderbook`, `/markets/trades`, historical trades. Docs: `https://docs.kalshi.com/llms.txt`. The orderbook may return only yes-bids and no-bids (implied ask = 1 − opposite bid); verify. |

**Optional free cross-checks:** predicting.top (keyless public leaderboard API), Dune's decoded Polymarket tables (requires an account, so skip unless needed), and Telonex free previews.

### 3.1 Historical on-chain dataset (`moose-code/polymarket-onchain-v1`, CC-BY-4.0)
- **Size and coverage:** about 127 GB of Zstd Parquet, from Polymarket's 2020 launch to **2026-04-24** (Polygon block 85,948,287). The v1 exchange kept producing fills until block 86,126,998, so a small tail is missing.
- **Tables:**
  - `order_filled/` (Hive-partitioned by `year=`): about 1.17B CLOB fills.
  - `user_position.parquet`: about 304M rows (`user`, `avgPrice`, `realizedPnl`, …).
  - `wallet.parquet`: proxy and Safe ↔ signer mapping.
  - `merge/`: merges.
  - About 25 entity tables in total, including splits, merges, redemptions, resolutions and FPMM-era trades.
- **Fill columns:** `maker`, `taker`, `makerAssetId`, `takerAssetId`, `makerAmountFilled`, `takerAmountFilled`, `timestamp` (unix seconds).
  - The cash leg is the side whose asset ID is `'0'` (USDC). Amounts are integers; divide USDC by 1e6.
  - If the maker gives USDC, the maker *bought* the outcome token from the taker; and so on. Price = USDC amount ÷ token amount.
- **Gotchas:**
  - Addresses are EIP-55 checksummed with casing that differs between tables. Always compare with `lower()` on the column, or filters silently return nothing.
  - Most accounts trade through a proxy or Safe. Resolve signers via `wallet.parquet`, and query both addresses with `IN`.
- **Query directly with DuckDB ≥ 1.5** (native `hf://` support), e.g. `SELECT … FROM 'hf://datasets/moose-code/polymarket-onchain-v1/order_filled/**/*.parquet' WHERE year IN (2025, 2026)`. Use partition pruning and column projection, and don't download the whole dataset.
- **After 2026-04-24:** use the Data API v2 `/v2/trades` or `/v2/activity` (keyset feeds by user or condition) for the selected wallets. This gives a weaker "Holdout B" (§8.1).

---

## 4. Domain concepts and gotchas (encode these as tests and comments)

1. **Markets, conditions and tokens.** A market (condition) has two outcome tokens, Yes and No, each with its own `token_id`. Normalize every position to a **canonical direction**: long No = short Yes, measured in Yes-equivalent shares. Consensus counting must use canonical direction, or a wallet "buying No" and another "selling Yes" look like different trades.
2. **Neg-risk (multi-outcome) events.** Mutually exclusive candidates in one event (e.g., "Who wins X?") are linked, and positions can be converted between them. Treat the **event** as the unit of a thesis. Cap exposure per event, and count consensus per (event, outcome).
3. **Correlated markets.** Different markets can express the same view (e.g., "Fed hikes in Dec," "another hike in 2026," "no cuts in 2026"). Cluster markets by event and by a simple thesis key (same tag plus overlapping resolution window plus a title-similarity threshold). Enforce an exposure cap per cluster.
4. **The positions feed is biased toward losers.** Winning positions get redeemed and drop off `/v2/positions`; worthless losers linger as `REDEEMABLE` at value 0. Reconstruct positions from trades and activity (or fills), never from the open-positions snapshot alone.
5. **Leaderboard PnL windows differ.** For `day`, `week` and `month`, PnL is marked equity change net of flows, so it moves with unrealized marks. For `all`, it's realized-only. They aren't comparable across windows. Leaderboard `volume` is in **shares, not USDC**.
6. **Splits, merges and redemptions** change positions without fills. Wallets that use them heavily are usually market makers or arbitrageurs. Flag them, and treat fill-only PnL reconstruction for them as unreliable.
7. **Resolution edge cases:** 50/50 or void resolutions, disputed resolutions (UMA), and markets resolved early. The payout per share can be 0, 0.5 or 1; handle all three.
8. **Short-duration crypto up/down markets** (5-, 15- and 60-minute) are latency and bot games. Exclude them from signals by default.
9. **Polymarket US quirks:**
   - Book sides are `bids` and `offers`; prices are decimal strings inside `Amount` objects.
   - Market `status` can be OPEN, CLOSED, RESOLVING, RESOLVED or HALTED.
   - There is no explicit winner field; use the settlement endpoint.
   - Small orders' fees can round to $0. Don't exploit this; enforce a realistic minimum order size (≥ $50 notional).
10. **Times:** store everything in UTC, using integer epoch milliseconds or timezone-aware timestamps.

---

## 5. Architecture and repo layout

**Stack:** Python 3.12, `uv` for environments, `duckdb` and `polars` (or pandas), `pyarrow`, `httpx` (async with retries), `pydantic` v2 models, `pyyaml`, `matplotlib`, `pytest`, `ruff`. Optional: `numba` for bootstrap speed. Keep dependencies lean.

```
polycopy/
  pyproject.toml
  README.md                  # 1-screen overview + current status link
  configs/
    default.yaml             # §10
    experiments/             # one yaml per experiment family
  src/polycopy/
    config.py
    util/{http.py, ratelimit.py, cache.py, time.py, log.py}
    venues/
      pm_global/{data_api.py, gamma.py, clob.py}
      pm_us/{gateway.py}
      kalshi/{public.py}
    catalog/{markets.py, categories.py, matching.py}   # global↔US↔Kalshi mapping
    history/{fills.py, prices.py, universe.py}         # DuckDB over HF + API backfill
    wallets/{positions.py, pnl.py, features.py, skill.py, classify.py, select.py}
    signals/{holdings.py, flow.py, single.py, clustering.py}
    sim/{fees.py, fills_backtest.py, fills_book.py, portfolio.py, exits.py}
    backtest/{engine.py, baselines.py, experiments.py, registry.py, stats.py}
    paper/{runner.py, state.py, reconcile.py}
    report/{phase_reports.py, weekly.py, charts.py}
  scripts/
    probe_endpoints.py
    build_catalog.py
    build_universe.py
    run_backtest.py
    run_paper_tick.py
    weekly_report.py
  tests/                     # unit tests + small fixtures (trimmed real responses)
  docs/{SPEC.md, STATUS.md, DECISIONS.md, DATA_NOTES.md}
  reports/{phase-1.md, phase-2.md, phase-3.md, phase-4.md, weekly/, figures/}
  data/                      # gitignored: caches, parquet
  .github/workflows/{ci.yml, paper.yml, weekly.yml, selection.yml}
```

Commit no data files to `main`. Paper-trader state lives on a separate `paper-state` branch (§11).

---

## 6. Core data model (parquet in `data/`, SQLite for paper state)

- `markets`: `condition_id, market_slug, event_id, event_slug, question, yes_token_id, no_token_id, neg_risk, category_raw, category_bucket, end_ts, resolved_ts, payout_yes (0/0.5/1/null), status`
- `fills_norm`: one row per (fill, participant): `ts, wallet, counterparty, token_id, condition_id, canon_dir (+1 buys Yes-equivalent / −1), shares, usdc, price_yes_equiv, role (maker/taker), source`
- `prices_5m`: `token_id, bucket_ts, vwap, taker_buy_vwap, taker_sell_vwap, volume_usdc, n_trades, last_price`
- `wallet_market_positions` (as-of snapshots or an event log): `wallet, condition_id, ts, net_yes_shares, cost_basis_usdc, realized_pnl_usdc`
- `wallet_features_asof`: `wallet, asof_ts, lookback_days, n_resolved_markets, volume_usdc, roi_cost_weighted, edge_mean, edge_lcb, skill_pvalue, skill_qvalue, maker_share, two_sided_share, split_merge_share, top_market_pnl_share, short_crypto_share, median_hold_hours, trades_per_day, category_mix{…}, label`
- `selected_wallets`: `asof_ts, method, rank, wallet, category (or 'all'), score`
- `signals`: `signal_id, ts, config_id, condition_id, event_id, outcome, canon_dir, n_wallets, wallets[], wallets_vwap_entry, market_price_at_signal, reason`
- `trades_sim`: `trade_id, signal_id, venue, ts_exec, side, shares, avg_price, fee, notional, fill_quality, unfilled_shares`
- `positions_sim` and `equity_daily`
- `experiments` registry: `config_id, config_hash, split, n_trades, n_events, roi, roi_ci_lo, roi_ci_hi, pnl, max_dd, brier_vs_mkt, timestamp, git_sha`

---

## 7. Strategy specification

### 7.1 Candidate universe (as-of *t*, using only data before *t*)
A wallet is a candidate if, over the lookback window L (default 180 days):
- it has at least `min_resolved_markets` (default 30) distinct markets that resolved before *t*;
- its volume is at least `min_volume_usd` (default $25k);
- it was active within the last 30 days.

### 7.2 Wallet selection methods (compare both)
- **`naive_pnl` (Darren's original):** rank by trailing 30-day marked PnL as of *t*, recomputed from fills the way the leaderboard defines its finite windows. Take the top N.
- **`skill` (recommended):**
  1. For each candidate, take its **resolved** markets in the lookback window. For each market *m*, compute the cost-weighted return r_m = PnL_m ÷ cost_m, including partial sells and the resolution payout.
  2. **Sign-randomization test:** under the null of no directional skill, flipping the canonical direction of each market's position flips the sign of its edge. Compute the p-value of the cost-weighted mean r against 10,000 random sign flips (a normal approximation is fine for n > 100). Control the false discovery rate across all candidates with Benjamini–Hochberg at q = 0.10.
  3. **Score** = lower confidence bound of the shrunk mean edge: shrink toward 0 with prior strength n0 = 30 markets, then subtract 1.64 × standard error.
  4. Optionally score per category bucket (min 15 markets in the bucket) and use a wallet's signals only in categories where it qualifies.
  5. Take the top N by score among wallets that pass FDR and the filters in §7.3.

### 7.3 Exclusion filters (applied before ranking, for both methods)
- **Market maker:** two-sided share > 0.3 (meaningful buys *and* sells of the same token within 24 h, by notional), or split/merge share > 0.2, or volume ÷ |PnL| > 200.
- **Bot or arb:** short-crypto share > 0.3, or median time between trades < 10 s with 24/7 activity, or frequently holding complementary outcomes in the same event.
- **One-hit wonder:** top single market > 50% of absolute PnL.
- Record each wallet's label (`skilled_directional`, `lucky`, `market_maker`, `bot_arb`, `one_hit`, `unskilled`) for reporting.

### 7.4 Signals (each is a config option)
In every case, *W* is the selected set as of the latest weekly refresh, and wallets count only in their qualified categories if per-category mode is on.

- **S1 `holdings` (Darren's consensus idea):** at decision time, count wallets in *W* with an open canonical position in (event, outcome) whose cost is ≥ `min_wallet_position_usd` and whose conviction ratio (position cost ÷ that wallet's median position cost over the lookback) is ≥ `min_conviction_ratio`. Net count = wallets long − wallets short. Signal when the net count ≥ k.
- **S2 `fresh_flow`:** count wallets in *W* that net-*increased* their canonical position in (event, outcome) by ≥ `min_wallet_add_usd` and ≥ 25% within the last `flow_window_hours`. Signal when the count ≥ k.
- **S3 `single_wallet`:** k = 1 using only the top-ranked wallets. This is a diagnostic to check whether consensus adds anything.

**Guards for all signals:**
- **Chase guard:** current entry price ≤ VWAP of the signaling wallets' entries + `chase_limit_cents`. For S1, use their average cost.
- **Price band:** `price_min` ≤ entry price ≤ `price_max`.
- **Time to resolution:** between `min_time_to_resolution_hours` and `max_time_to_resolution_days`.
- **Category:** not in excluded categories; skip markets that are already live in-play unless the latency analysis supports it.
- **No duplicates:** no existing bot position in the same event or thesis cluster. One entry per signal episode; an episode ends when the net count falls below k.

### 7.5 Sizing and risk (paper bankroll: $10,000)
- Per trade: `per_trade_pct` (2%) of current equity.
- Per event or thesis cluster: at most `max_event_pct` (6%).
- Total deployed: at most `max_deployed_pct` (60%).
- Never take more than `max_book_participation` (25%) of the displayed depth within the chase limit (paper), or of the recent 1-hour volume (backtest).
- Minimum order: $50 notional.

### 7.6 Exits
- **E1 `hold_to_resolution`** (default).
- **E2 `follow_exit`:** exit at the bid when the signaling wallets' aggregate canonical position falls below 50% of its peak since entry.
- Report both.

### 7.7 Execution models
- **Backtest:**
  - Execution time = signal time + `latency_minutes`.
  - Buy price = taker-buy VWAP for that token over [t_exec, t_exec + 10 min], plus a 1¢ penalty, plus slippage of 0.5¢ per (order notional ÷ trailing 1-hour volume × 10), capped at the chase limit.
  - If taker-buy volume in the window is under $200, use the last trade + 1.5¢.
  - If there are no trades within 60 minutes of t_exec, the trade is **unfillable** (count it; don't take it).
  - Sells mirror this using taker-sell prices.
  - Polymarket publishes no historical order books, so this approximation is necessary. Document it.
- **Paper (live):** walk the actual venue book (`offers` for buys) up to size, within the chase limit and the participation cap. Partial fills are allowed. Record unfilled shares and real latency (wallet trade timestamp → our tick).
- **Fees:**
  - Primary: Polymarket US Θ = 0.095, banker's-rounded per order.
  - Also report Kalshi (0.07, rounded up) and Polymarket global (live `fee-rate`) as sensitivity cases.
  - Apply *current* fees to the whole backtest history, even though fees didn't exist earlier, because current fees are what a live bot would pay.

---

## 8. Evaluation methodology

### 8.1 Time splits (by signal date; wallet lookbacks may extend earlier)
| Split | Dates | Use |
|---|---|---|
| Train | 2024-07-01 → 2025-09-30 | parameter search |
| Validation | 2025-10-01 → 2026-01-31 | shortlist ≤ 3 configs |
| Holdout A | 2026-02-01 → 2026-04-24 | touch **once** per shortlisted config |
| Holdout B | 2026-04-25 → latest | API-sourced; weaker; report separately |

- Trades entered in a split are evaluated at their real resolution, which is known now. That's fine; decisions still use only past data.
- Report train results with and without Oct–Nov 2024 (the US election period was unusual).
- Refresh wallet selection weekly in sim time. The decision tick is every 15 minutes of sim time.

### 8.2 Baselines (every strategy result must be shown next to these)
- **B0 market-matched random:** for each strategy trade, 20 random taker buys in the same category bucket, the same price bucket (±2¢), the same time-to-resolution bucket and the same entry week, with the same sizing, fees and fill model. This measures "the price is right minus fees" and should be about −fees.
- **B1 naive copy:** `naive_pnl` selection + S1 holdings (Darren's original).
- **B2 single best wallet:** S3 with N = 1.

### 8.3 Metrics
- **Returns:** net PnL; ROI = Σ net PnL ÷ Σ cost; per-trade edge (payout − entry − fee); hit rate.
- **Breakdowns:** by price bucket, category, time-to-resolution, latency and number of agreeing wallets.
- **Risk:** max drawdown on daily equity, exposure, turnover, and Brier score of entry prices vs outcomes compared with the market's.
- **Fill quality:** unfillable rate and fill quality.
- **Concentration:** PnL share of the top event.
- **Latency decay curve:** net ROI vs latency ∈ {1, 5, 15, 30, 60, 240} minutes. This decides how fast the paper trader must poll.
- **Capacity curve:** net ROI vs per-trade notional ∈ {$50, $200, $1k, $5k}.
- **Uncertainty:** 95% CIs by block bootstrap over events (10,000 resamples).

### 8.4 Guarding against overfitting
- **Staged search on Train:** first selection method × N (with default signal), then signal parameters, then exits. **At most 300 configs total**, all logged in the experiments registry with their config hash and git SHA.
- **Validation:** pick at most 3 configs, prefer simpler ones, and require stability (neighboring parameter values must also be positive).
- **Holdout A:** run once. Report how many configs were tried, alongside the result.

---

## 9. Phases, tasks, acceptance criteria and gates

### Phase 0: Bootstrap
- Create the repo (private, unless it already exists), add `pyproject.toml`, ruff and pytest, and CI that runs only on push/PR to `main` with a uv cache.
- Commit this spec as `docs/SPEC.md`. Create STATUS, DECISIONS and DATA_NOTES.
- Write `scripts/probe_endpoints.py`:
  - hit every endpoint in §3, check auth, response schemas and pagination;
  - check geoblock behavior for CLOB and Data API reads from this environment;
  - check the HF dataset with DuckDB (count one partition);
  - save trimmed sample responses to `tests/fixtures/`;
  - document everything in DATA_NOTES.
- Check disk, CPU and RAM (`df -h`, `nproc`, `free -g`) and plan data handling to fit (§9 Phase 1 uses two passes for this reason).
- **Done when:** probes pass or are documented, CI is green, and STATUS is updated.

### Phase 1: Data layer and catalog
1. **Clients:** HTTP clients with rate limiting, retries and a disk cache. Pydantic models for the fields we use.
2. **Market catalog:** all global markets and events from Gamma (open and closed, paginated), cached to `data/markets.parquet`. Map tags to category buckets: politics, geopolitics, sports, esports, crypto, crypto_short, economics_finance, tech_ai, culture, weather, other. Store resolution payouts.
3. **Universe pass 1** (one streaming DuckDB pass over `order_filled` for 2024-01 → 2026-04): per wallet-month volume, trade count and distinct tokens. Save the result, then derive candidate wallets with generous thresholds (e.g., ≥ $10k volume in any 180-day window). Resolve proxies and signers.
4. **Pass 2:** extract normalized fills (`fills_norm`) **only for candidate wallets**, plus `prices_5m` for **all tokens** (aggregate during the pass). Write monthly parquet files.
5. **Post-April backfill:** for candidate wallets active in March–April 2026, page `/v2/trades` from 2026-04-24 to now. Store it with `source = 'api'`.
6. **Sanity checks:**
   - ≥ 99% of fill token IDs map to catalog markets;
   - monthly volumes are plausible (state your comparison);
   - 20 random fills match the Data API's `/v2/trades` for the same wallet.
- **Deliverable:** `reports/phase-1.md` covering data coverage, sizes, gaps and known biases.

### Phase 2: Wallet analytics
1. **Position and PnL engine** (`wallets/positions.py`, `pnl.py`): an event-sourced per-wallet, per-condition canonical position with average cost, realized PnL from sells and payouts, and mark-to-market at any *t* (last price before *t*). Write unit tests with hand-computed scenarios: partial sells, No-side buys, 0.5 payouts, neg-risk events.
2. **Validation:** compare realized PnL against `user_position.realizedPnl` and the Data API for 50 wallets of mixed types. Target: median absolute error < 5% for wallets with split/merge share < 0.1. Document the failures.
3. **Features, the skill test (§7.2) and the classifier (§7.3)**, computed as-of weekly dates.
4. **Replication check:** split resolved markets into two halves (alternating events, or two time halves) and measure how many wallets classified skilled in A remain skilled in B. Compare this to the paper's 44% as a pipeline sanity check (it needn't match exactly; explain large differences).
5. **Landscape report** (`reports/phase-2.md`):
   - what fraction of wallets falls in each label;
   - how today's monthly top 50 would be labeled;
   - the category mix of skilled wallets;
   - overlap between `naive_pnl` and `skill` selections.

### Phase 3: Backtest and Gate G1
1. **Engine:** sim-time loop (15-minute ticks), weekly selection refresh, signals, fills, portfolio constraints, exits, resolution payouts, daily equity.
2. **Leakage test:** run the engine with all data after *t* deliberately corrupted (e.g., outcomes shuffled for markets resolved after *t*, and prices after *t* randomized). Decisions up to *t* must be byte-identical. This test must pass in CI.
3. **Baselines** B0–B2 and the experiment registry.
4. **Run** the staged search on Train, then the Validation shortlist, then Holdout A once, then Holdout B.
5. **Decay and capacity curves** for the shortlisted configs.
6. **Report** (`reports/phase-3.md`): an executive summary (5 bullets), a results table vs baselines, charts (equity curves, decay curve, capacity curve, ROI by price bucket and category), the G1 decision, and a full list of caveats.

**Gate G1 (pre-registered; all must hold for at least one shortlisted config on Holdout A):**
- (a) ≥ 150 resolved trades across ≥ 75 distinct events.
- (b) Net ROI (Polymarket US T1 fees) > 0, with a block-bootstrap one-sided p < 0.05.
- (c) Beats B0 by ≥ 2 percentage points of ROI, and beats B1.
- (d) No single event contributes > 20% of net PnL.
- (e) Net ROI is positive in at least 2 of 3 equal sub-periods of Holdout A.
- (f) Net ROI stays > 0 at latency ≥ the planned paper cadence (default 30 minutes).
- (g) The result doesn't depend on a single category; report results with the top category removed.

**If G1 fails:** you may do **up to 2 redesign rounds** that use only Train and Validation (e.g., per-category selection, different exits, stricter chase guard). After that, the only holdout left is Holdout B, used once. If it still fails, follow Operating Rule 10 and stop.

### Phase 4: Venue mapping, live paper trader and Gate G2 (only after G1 passes)
1. **Polymarket US catalog and matcher** (`catalog/matching.py`):
   - Map global (event, market) to the US market and side. Try, in order: exact slug, normalized slug, then normalized question text (lowercased, punctuation and stop-words removed, dates canonicalized) plus end date within ±1 day plus outcome-label agreement.
   - Assign a match confidence. **Auto-validate:** for matched liquid markets, |mid_US − mid_global| should usually be < 5¢. Log disagreements and drop low-confidence matches.
   - Report coverage: what share of Validation, Holdout and live signals had a confident US match, by category.
2. **Kalshi matcher (conditional):** build it if US coverage of signal markets is < 60%, or if the Kalshi fees and spreads give materially better net prices. Use the same approach with Kalshi `events/markets` titles, close times and outcomes.
3. **Paper trader** (`paper/runner.py`, run by `scripts/run_paper_tick.py`). Each tick:
   1. Load state.
   2. Fetch new trades and activity since the stored cursor for each wallet in *W*, and update their canonical positions.
   3. Compute signals with the frozen G1 config.
   4. For each new signal, look up the venue match. Fetch the US book (and the Kalshi book if matched, and the global book for reference). Simulate the fill per §7.7, recording fills on all available venues and making the **primary** decision on the primary venue.
   5. Check open paper positions for exits and resolutions (US settlement endpoint, Gamma resolution).
   6. Write signals, trades, positions and equity to state. Commit to the `paper-state` branch.
4. **Selection refresh:** a weekly job recomputes *W* with the same algorithm. Wallet histories come from the Data API: closed positions and trades for candidates gathered from v2 leaderboards (PnL and volume boards, all categories, `month` and `all` windows, deep pages) ∪ the Phase-1 candidate universe ∪ holders of the top 200 markets by volume.
   - **Parity check:** on a recent date that both the dataset and the API cover, the API-built *W* must overlap the dataset-built *W* by ≥ 80%. Otherwise widen the candidate sources and document it.
5. **Ops:** see §11. Run for **at least 8 weeks and at least 100 resolved paper trades**, whichever comes later, capped at 16 weeks.
6. **Weekly report** (`reports/weekly/YYYY-[Www.md](https://Www.md)`, plus a GitHub issue, §11): equity curve, the new signals and trades table, resolved PnL, venue gap (US vs global entry price), fill rates, latency distribution, and comparison to the backtest's expected ROI band.

**Gate G2:**
- (a) The minimum run length is met.
- (b) Net ROI on primary-venue paper fills ≥ 0.
- (c) Realized ROI falls inside the backtest's 80% interval for the same number of trades.
- (d) Median latency ≤ the latency assumed in G1.
- (e) No unresolved operational incidents.
- **Write `reports/phase-4.md`** with the G2 decision and stop. Phase 5 needs Darren.

### Phase 5: Real-money design (needs Darren's explicit approval; design document only)
If G2 passes, write `docs/LIVE_DESIGN.md` covering:
- venue choice and the fee tier path;
- hard risk limits (max per trade, per day, drawdown kill-switch);
- key handling (keys stay on Darren's machine or in GitHub Actions secrets that he creates himself);
- a manual approval mode for the first 50 trades;
- legal and state-availability checks for Ohio.

Implement nothing further until Darren approves in an issue comment.

---

## 10. Default config (`configs/default.yaml`)

```yaml
bankroll_usd: 10000
universe:
  lookback_days: 180
  min_resolved_markets: 30
  min_volume_usd: 25000
  active_within_days: 30
selection:
  method: skill            # skill | naive_pnl
  n_wallets: 20            # sweep: 5, 10, 20, 40
  refresh: weekly
  fdr_q: 0.10
  shrink_n0: 30
  per_category: false      # sweep true/false
  min_category_markets: 15
filters:
  mm_two_sided_share_max: 0.30
  mm_split_merge_share_max: 0.20
  mm_volume_to_abs_pnl_max: 200
  bot_short_crypto_share_max: 0.30
  one_hit_top_market_share_max: 0.50
signal:
  type: fresh_flow         # holdings | fresh_flow | single_wallet
  k_min: 2                 # sweep: 1 (single only), 2, 3, 5
  flow_window_hours: 24    # sweep: 1, 6, 24, 72
  min_wallet_position_usd: 500
  min_wallet_add_usd: 250
  min_conviction_ratio: 1.0
  chase_limit_cents: 5     # sweep: 2, 5, 10, null
  price_min: 0.05
  price_max: 0.92
  min_time_to_resolution_hours: 6
  max_time_to_resolution_days: 120
  exclude_categories: [crypto_short]
  conflict_rule: net
sizing:
  per_trade_pct: 2.0
  max_event_pct: 6.0
  max_deployed_pct: 60
  max_book_participation: 0.25
  min_order_usd: 50
exit:
  rule: hold_to_resolution # hold_to_resolution | follow_exit
  follow_exit_threshold: 0.5
execution:
  latency_minutes: 30      # decay sweep: 1, 5, 15, 30, 60, 240
  backtest_window_minutes: 10
  backtest_penalty_cents: 1.0
  unfillable_after_minutes: 60
  venue_priority: [polymarket_us, kalshi]
  reference_venue: polymarket_global
fees:
  polymarket_us: {theta: 0.095, rounding: bankers_cent}   # T1; verify docs.polymarket.us/fees.md
  kalshi: {coeff: 0.07, rounding: ceil_cent}              # verify docs.kalshi.com
  polymarket_global: {source: clob_fee_rate}
stats:
  bootstrap_resamples: 10000
  block: event
seed: 1729
```

---

## 11. Paper-trader operations (GitHub Actions)

- **Workflows:**
  - `paper.yml` runs on cron `7,37 * * * *` (every 30 minutes, off the top of the hour to avoid scheduler congestion). Add `workflow_dispatch` and `concurrency: {group: paper, cancel-in-progress: false}`.
  - `selection.yml` runs weekly (e.g., Sunday 06:23 UTC).
  - `weekly.yml` runs Monday 12:41 UTC.
- **Minutes budget:** private repos on GitHub Free get 2,000 Actions minutes a month, each job is rounded up to a whole minute, and public repos are free.
  - 48 ticks/day × 30 days = 1,440 minutes if each tick finishes in under 60 s.
  - Keep ticks under 60 s: uv cache, minimal deps installed for the tick job, a small number of requests, and no heavy recomputation.
  - Measure real usage in week 1 and record it in DATA_NOTES.
  - If the budget won't fit, or the decay curve says 30 minutes is too slow, see §13. Don't make the repo public on your own.
- **State:** a SQLite DB plus daily CSV snapshots, on an orphan branch `paper-state`. Each tick checks out that branch, updates it and commits as `paper: tick <ts>`. Prune raw payload caches older than 14 days. Keep the DB under 50 MB.
- **Failure handling:**
  - Retries with backoff.
  - After 3 consecutive failed ticks, open one issue titled "Paper trader failing" (deduplicated by title) and close it automatically on recovery.
  - Ticks must be idempotent; use the stored cursors.
- **Notifications:** Darren doesn't want to babysit this.
  - Issues are opened only for: phase completions ("Phase N complete: <one-line result>"), gate decisions, the paper-trader-failing alert, and the §13 needs.
  - Weekly reports post as a single comment on one pinned issue, "Weekly paper reports," which keeps notifications to one thread.

---

## 12. Report templates

**Phase report header:**
```
# Phase N — <title>
Result: <one sentence>
Decision: <proceed / stop / redesign>
Key numbers: <3–6 bullets with CIs>
What could make this wrong: <bullets>
Next: <bullets>
```

**Weekly paper report:**
- Equity and net PnL (week and cumulative).
- Open exposure.
- Trades entered and resolved, with a table: market, venue, entry, size, fee, signaling wallets, status, PnL.
- Venue gap.
- Fill rate.
- Latency median and p90.
- Cumulative realized ROI vs the backtest band.
- Incidents.

---

## 13. When to contact Darren (via a GitHub issue titled "Needs Darren: …")

Only for:
1. **Hosting:** the GitHub Actions minutes budget can't fit after optimization, or the latency analysis requires polling faster than 30 minutes. Lay out the options with costs: make the repo public (free Actions), GitHub Pro, a small VPS, or his own Linux machine. Recommend one.
2. **Paid data or an account** is genuinely required (unlikely).
3. **A ToS or legal ambiguity** you can't resolve from public documentation.
4. **Gate decisions:** informational only (G1 pass/fail, G2 pass/fail). No reply is needed unless Phase 5 is proposed.
5. **Phase 5 approval.**

Make all other decisions yourself and log them in `docs/DECISIONS.md`.

---

## 14. Definition of done
- [ ] Phases 0–3 complete with reports. The G1 decision is recorded.
- [ ] If G1 passed: Phase 4 has run its minimum duration, the G2 decision is recorded, and the weekly reports exist.
- [ ] `README.md` explains the project and the current status in under one screen.
- [ ] `docs/STATUS.md` reflects the final state, and the repo is clean, tested and reproducible (`uv run scripts/run_backtest.py --config configs/<final>.yaml` reproduces the headline numbers).

---

## Appendix A: Fee formulas
```
pm_us_fee(C, p, theta=0.095)   = bankers_round_cents(theta * C * p * (1 - p))
kalshi_fee(C, p)               = ceil_cents(0.07 * C * p * (1 - p))
pm_global_fee(C, p, rate)      = C * rate * p * (1 - p)      # rate from /fee-rate per token
breakeven_prob(p, fee_per_ct)  = p + fee_per_ct               # taker buy held to resolution
```

## Appendix B: Reference links
- Polymarket docs: https://docs.polymarket.com (index: /llms.txt). Data API v2 OpenAPI: https://data-api.polymarket.com/v2/openapi.json
- Polymarket US docs: https://docs.polymarket.us (index: /llms.txt). Fees: https://docs.polymarket.us/fees.md
- Kalshi docs: https://docs.kalshi.com (index: /llms.txt)
- HF dataset: https://huggingface.co/datasets/moose-code/polymarket-onchain-v1 (write-up: https://docs.envio.dev/blog/polymarket-onchain-data)
- Skill paper coverage: https://www.theblock.co/post/398902/skilled-polymarket-traders-are-a-3-minority-and-everyone-else-funds-their-gains-study
- Reichenbach & Walther: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5910522
- "Who wins and who loses…" discussion: https://news.ycombinator.com/item?id=48221877
