# DATA NOTES

Endpoint quirks, schemas, and verification results (SPEC §3). Probes: `uv run python scripts/probe_endpoints.py`; trimmed real responses live in `tests/fixtures/`.

**Probe run 2026-11-15 from the cloud dev container (US region): 28/28 PASS.** No geoblock on any read: data-api, gamma, clob, gateway.polymarket.us, Kalshi, HF all answer 200 unauthenticated. Re-verify from GitHub Actions runners in Phase 4 before relying on them there.

## Polymarket Data API v2 (`data-api.polymarket.com`)

- **Envelope:** `{data: [...], pagination: {limit, offset, has_more, next_cursor}}`. `next_cursor` is base64 of `{data:{type,params},sig}` — **signed**, so pages can only be walked via the cursor; you cannot fabricate offsets. Keyset params inside (`ts`, `sq`) show trades pages are (timestamp, sequence)-keyed.
- **v1 is retired:** `GET /leaderboard` → HTTP 410 `{"error":"Data API v1 has been retired","migration":...}`.
- `/v2/leaderboard`: `sort_by` must be uppercase `PNL`|`VOLUME` (lowercase → 400 `{error, code, parameter, retryable, trace_id}`). Params: `time_period` (`day|week|month|all`), `category`, `user`, `limit`, `cursor`. Row: `rank, user_id, pnl, volume, user_name, x_username, verified`. Spec warning stands: `volume` is in shares, finite-window `pnl` is marked (includes repricing).
- `/v2/trades`: params `user, condition, event_id, side, taker_only, filter_type, filter_amount, start, end, limit, cursor`. Row: `proxy_wallet, side (BUY|SELL), token_id, condition_id, size, price, timestamp (unix s), outcome, outcome_index, title, slug, event_slug, transaction_hash` + profile fields. `taker_only` will matter for taker-VWAP fill modeling.
- `/v2/positions`: row includes `current_size, avg_price, entry_cost_usdc, entry_fees_usdc, total_cost_usdc, current_price, current_value, total_size, realized_pnl, unrealized_pnl, total_pnl, status, redeemable, mergeable, negative_risk, archived, event_id, outcome, outcome_index, opposite_token_id, end_date, first_entry_at, last_event_at`. Note **`entry_fees_usdc` > 0 observed** — global Polymarket charges taker fees on some categories now (sports at least).
- `/v2/activity`: like trades but `type` field; observed `TRADE` and **`MIGRATION`** (not in SPEC §4's split/merge/redeem list — handle it in position reconstruction; for MIGRATION rows `side=""`, `usdc_size=0`, `price=0`). Param `exclude_deposits_withdrawals` implies DEPOSIT/WITHDRAWAL types exist too.
- `/v2/prices-history`: `token_id, interval, bucket_seconds, start, end, as_of, limit, cursor` → `{timestamp, price, resolution_seconds}`. The **`as_of`** param may give point-in-time views; test before trusting.
- `/v2/holders`: by `condition`, has `min_balance`, `include_pnl`.
- `/v2/resolutions`: by `condition|question_id|event_id` → `status ('posed', …), was_disputed, extended_review, proposed_price, reproposed_price, price, transaction_hash`. **Units unresolved:** a points-O/U market showed `price: '69'` — looks like a raw UMA value (possibly the scalar points total), *not* 0/0.5/1. Do NOT use this field for payouts until decoded; prefer Gamma `outcomePrices`/UMA status for resolved payout (Phase 1 task).
- Other endpoints that exist: `/v2/user-pnl` (interval/fidelity series), `/v2/user-stats`, `/v2/user-volume`, `/v2/biggest-winners`, `/v2/oi`, `/v2/live-volume`, `/v2/tokens`, `/v2/value`, `/v2/status`, combos variants.

## Gamma (`gamma-api.polymarket.com`)

- `/markets`, `/events` return **bare JSON arrays** (no envelope); offset pagination works (`limit`, `offset`, `order`, `ascending`, `closed`).
- **Stringified JSON inside JSON:** `outcomes`, `outcomePrices`, `clobTokenIds` are JSON-encoded strings — parse twice.
- Market fields: `conditionId, questionID, slug, negRisk, events[{id,ticker,slug,title}], umaResolutionStatus, resolvedBy, restricted, enableOrderBook, orderPriceMinTickSize, orderMinSize, gameStartTime, secondsDelay, makerBaseFee, takerBaseFee, volume24hr/1wk/1mo/1yr, liquidity, startDate, endDate, createdAt, updatedAt`. `gameStartTime` is the in-play detector for sports.
- `takerBaseFee: 1000` observed on an esports market, matching CLOB `fee-rate`.

## CLOB read-only (`clob.polymarket.com`)

- `/book?token_id=` → `{market (condition), asset_id, timestamp (ms string), hash, bids: [{price,size} strings], asks: [...], min_order_size, tick_size, neg_risk, last_trade_price}`.
- `/fee-rate?token_id=` → `{"base_fee": 1000}` on a sports token. **Units TBD** (bps of notional vs the p(1−p) formula coefficient). Decode before using global fees in money math — global fees are only a sensitivity case (SPEC §7.7).
- `/prices-history?market=<token_id>&interval=1d&fidelity=<minutes>` → `{history: [{t, p}]}`.

## Polymarket US gateway (`gateway.polymarket.us`)

- `/v1/markets` → `{markets: [...]}`. Fields: `id, question, slug, category, marketType, sportsMarketType(V2), status (MARKET_STATUS_OPEN|_RESOLVED|…), active, closed, outcomes/outcomePrices (stringified JSON), marketSides [{id, marketSideType, identifier}], feeCoefficient, orderPriceMinTickSize, minimumTradeQty, gameStartTime, startDate, endDate, tags`.
- **`feeCoefficient` (e.g. 0.095) ships per market** — fee math can be driven by venue data instead of a hardcoded Θ. (SPEC's T1 Θ=0.095 confirmed live.)
- **Filter semantics are unreliable:** `active=true` returned a resolved 2025 NFL market. Filter client-side on `status`.
- Pagination of `/v1/markets` not yet verified (limit honored; cursor/offset unknown) — resolve in Phase 1/4.
- `/v1/markets/{slug}/book` → `{marketData: {marketSlug, bids: [{px: {value: "0.1590", currency: "USD"}, qty: "172.0000"}], offers: [...]}}` (YES-denominated).
- `/v1/markets/{slug}/bbo` exists — lightweight top-of-book for paper ticks.
- `/v1/markets/{slug}/settlement` → `{slug, settlement: 1}` (resolved NFL market). 0.5/void representation not yet observed.
- Docs also list `/v1/events` (by slug/id) and price-history (`symbol` = market slug).

## Kalshi (`api.elections.kalshi.com/trade-api/v2`)

- `/markets?limit&status=open` → `{markets:[...], cursor}`; prices now in decimal-string dollar fields (`yes_bid_dollars`, `no_ask_dollars`, `last_price_dollars`) plus `_fp` fixed-point sizes. `price_ranges` shows **sub-cent ticks in the tails** (e.g. step 0.0001 below 1¢) — fee/tick math must not assume whole cents.
- `/markets/{ticker}/orderbook` → `{orderbook_fp: {yes_dollars: [], no_dollars: []}}` (not the docs' `orderbook` key). Bids per side only; ask implied as 1 − opposite-side bid, per SPEC §3. Level shape unverified (sampled book was empty) — check on a liquid ticker before Phase 4 use.
- `/markets/trades?limit=` works globally with cursor pagination.
- Lots of `KXMVE…` multi-leg combo markets with near-zero volume pollute `status=open` listings — filter them out for matching.

## HF dataset (`hf://datasets/moose-code/polymarket-onchain-v1`)

- DuckDB (1.4+ installed via uv) reads `hf://` through this environment's proxy without auth. `count(*)` on a partition is metadata-only and fast (~2 s).
- `order_filled` cols: `id, takerAmountFilled, fee, transactionHash, timestamp, orderHash, maker, taker, makerAssetId, takerAssetId, makerAmountFilled, year` (hive partition).
- **`year=2022` holds only 3,161 fills** — the CLOB effectively starts 2023; 2020–2022 volume is FPMM-era (separate tables). Our backtest window (2024-07+) is all CLOB.
- `user_position.parquet`: `id, user, tokenId, amount, avgPrice, realizedPnl, totalBought`.
- `wallet.parquet`: `id, signer, type, balance, lastTransfer, createdAt` (id = proxy/Safe address, signer = EOA).
- Address casing differs between tables (EIP-55): always compare with `lower()`.

## Rate limits

No numeric limits published for any of these hosts; nothing hit 429 at our probe pace. Client policy (SPEC §0.4): ≤5 req/s per host, exponential backoff, honor `Retry-After`, descriptive UA `polycopy-research/0.x`.
