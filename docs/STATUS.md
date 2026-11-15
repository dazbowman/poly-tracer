# STATUS

> Read this first when resuming. Keep it current: what's done, what's next, open blockers.

**Last updated:** 2026-11-15 (session 1)

## Where things stand

- **Phase 0 (bootstrap): done pending CI confirmation on GitHub.**
  - [x] Repo scaffolded: pyproject (uv, ruff, pytest), CI, config loader + default.yaml, docs.
  - [x] Spec committed verbatim to `docs/SPEC.md` (first commit).
  - [x] Environment checked: 4 CPU, 15 GB RAM, ~31 GB free disk. Python 3.13 locally, project pins 3.12 via uv. All data hosts reachable from this environment (no geoblock on reads).
  - [x] `scripts/probe_endpoints.py`: 28/28 probes PASS; 21 trimmed fixtures in `tests/fixtures/`; findings in DATA_NOTES (highlights: v1 leaderboard is HTTP 410-retired; leaderboard `sort_by` wants uppercase `PNL`; activity has a `MIGRATION` type; PM-US ships `feeCoefficient=0.095` per market; Kalshi orderbook key is `orderbook_fp`; `/v2/resolutions.price` units are suspect — don't use for payouts yet).
  - [ ] CI green on GitHub (push + check pending).

## Next

1. Push, confirm CI is green → Phase 0 closed.
2. Phase 1 (data layer and catalog), in order:
   a. `util/` HTTP client with rate limit + retries + disk cache; pydantic models for used fields.
   b. Gamma catalog → `data/markets.parquet` (open+closed, category buckets, payouts). Resolve the payout question (Gamma `outcomePrices` of resolved markets vs `/v2/resolutions` — decode units first).
   c. Universe pass 1: DuckDB streaming aggregate over HF `order_filled` 2024-01→2026-04 (wallet-month volume/trades/tokens) → candidate wallets (≥$10k vol in any 180d window). Resolve proxies via `wallet.parquet`.
   d. Pass 2: `fills_norm` for candidates + `prices_5m` for all tokens, monthly parquet.
   e. API backfill 2026-04-24→now for candidates active Mar–Apr 2026 (`source='api'`).
   f. Sanity checks (≥99% token→market mapping; plausible monthly volumes; 20 fills cross-checked vs `/v2/trades`) → `reports/phase-1.md`.

## Open blockers

- None.

## Standing constraints (never stale)

- Paper trading only; no keys, no accounts, no geoblock circumvention (SPEC §0).
- Work happens on branch `claude/dreamy-gates-8wlal5`; `main` only moves by PR (see DECISIONS d-002).
- Disk allowance ~31 GB: never download the 127 GB HF dataset; stream via DuckDB `hf://` with partition pruning and projection.
