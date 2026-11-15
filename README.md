# polycopy

Can copying the positions of skilled Polymarket wallets beat the market after
realistic fees, latency and slippage? This repo finds out honestly — **paper
trading only**, public keyless data only, and the pre-registered expectation is
that the answer is *no* (prediction-market prices are well calibrated, copying
is a taker strategy, and a copier never gets the leader's entry price).

- **Spec and source of truth:** [docs/SPEC.md](docs/SPEC.md)
- **Current status:** [docs/STATUS.md](docs/STATUS.md)
- **Decision log:** [docs/DECISIONS.md](docs/DECISIONS.md) · **Data notes:** [docs/DATA_NOTES.md](docs/DATA_NOTES.md)
- **Reports:** [reports/](reports/)

**How it works:** signals come from public on-chain Polymarket (global) wallet
activity; paper execution is simulated against Polymarket US (and Kalshi) order
books — the venues the owner can legally use. Phases: 0 bootstrap → 1 data
layer → 2 wallet analytics → 3 backtest (Gate G1) → 4 live paper trader
(Gate G2). Real-money work (Phase 5) is design-only and needs explicit owner
approval.

```bash
uv sync                 # install
uv run pytest           # tests
uv run ruff check .     # lint
uv run python scripts/probe_endpoints.py   # re-verify data sources
```
