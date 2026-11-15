# Phase 2 — Wallet analytics

Result: the features/skill/classifier pipeline is built and validated — position replay matches chain-computed realized PnL to 0.002% (median), and the A/B-halves skill persistence (45.1% vs 5.0% base rate) reproduces the literature's ~44%. Skilled wallets exist (≈2–4% of gated candidates) and their skill persists; the naive top-PnL leaderboard overlaps the skill selection 0/20.
Decision: proceed to Phase 3 backtest.

## Candidate landscape (as-of 2026-02-02, lookback 180d, gates: ≥30 resolved markets, ≥$25,000 volume, active ≤30d)

Candidates passing gates: **20,510**

| label | wallets | share | lookback volume |
|---|---|---|---|
| market_maker | 12,162 | 59.3% | $3,811M |
| unskilled | 4,564 | 22.3% | $1,823M |
| bot_arb | 3,076 | 15.0% | $405M |
| skilled_directional | 451 | 2.2% | $396M |
| one_hit | 257 | 1.3% | $57M |

Hyperactive wallets (>200 fills/day or >100k fills) were pre-excluded from feature
extraction and are bot/MM-class by construction (DECISIONS d-013); shares above are
within the extracted candidate set.

## Category mix of skilled wallets

| top category | skilled wallets |
|---|---|
| crypto | 141 |
| geopolitics | 134 |
| politics | 80 |
| sports | 40 |
| economics_finance | 17 |
| culture | 15 |
| weather | 11 |
| esports | 7 |

## naive_pnl vs skill selection (N=20, same date)

- overlap: **0/20** wallets
- skill picks' median lookback ROI: 104.4%

## Today's monthly leaderboard top-50, labeled

| label | wallets |
|---|---|
| not_in_universe | 45 |
| unskilled | 2 |
| market_maker | 2 |
| skilled_directional | 1 |

(`not_in_universe` = not in the 2024-01→2026-04 extract set or below gates as of the
reference date. This comparison is loose by construction — the live leaderboard is from
2026-11 while our data ends 2026-04-24, so accounts that started after April cannot
appear — but the direction is consistent with the papers' finding that top leaderboard
PnL is dominated by variance and turnover, not persistent skill.)

## Position-engine validation (vs chain user_position)

50 sampled wallets were replayed through the position engine (fills + splits/merges/redemptions, subgraph moving-average convention) against the chain's own `user_position.realizedPnl`: median |relative error| **0.002%** (SPEC target <5%); worst wallet 1.25%; wallets with split/merge share up to 0.32 agree to 1e-10.

## Replication check (A/B halves)

Each wallet's resolved markets were split into two halves by event-hash parity and the skill test run independently per half (wallets with ≥15 resolved markets in both halves: 66,400). Skilled in A: 2,973 (4.5%). **P(skilled in B | skilled in A) = 45.1%** against a 5.0% base rate — a 9× lift, matching the ~44% out-of-sample persistence reported by Gómez-Cram et al.

## What could make this wrong

- **The sign-flip null treats markets as independent**, but markets inside one event are correlated (complementary legs, same game). Per-wallet p-values are therefore somewhat anti-conservative for event-concentrated wallets; the A/B split is by event hash (whole events stay in one half), so the persistence number does not inherit this bias, and Phase 3 inference uses event-block bootstraps.
- **Some "skill" is likely uncopyable speed.** Mid-2025 top scorers include wallets with 2,000+ resolved markets at ~0.85 cost-weighted ROI on ~$50k volume — a live-event-sniper profile. The 30-minute copy latency and the §7.7 execution model exist to price exactly this; features alone cannot distinguish copyable edge from speed.
- **Naive-selection marks are last-trade, not midpoint** (d-007), and 2026 fee-era buy sizes are gross of token-side taker fees (d-015, ≤5% on affected legs).
- Labels and the landscape table are one as-of date (2026-02-02); label shares drift with the platform mix (2026 dates run to 69% market_maker among candidates).
- The lookback convention credits a market to the window of its *resolution* date using its whole fill history — a wallet's early entries can predate the window.

## Next

- Phase 3: smoke backtest, staged Train search (≤300 configs), Validation shortlist ≤3, Holdout A once, Holdout B per d-012. Gate G1 decides whether any variant beats the matched-baseline band after costs.
