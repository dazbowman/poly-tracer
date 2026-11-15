# STATUS

> Read this first when resuming. Keep it current: what's done, what's next, open blockers.

**Last updated:** 2026-11-15 (session 1)

## Where things stand

- **Phase 0 (bootstrap): in progress.**
  - [x] Repo scaffolded: pyproject (uv, ruff, pytest), CI, config loader + default.yaml, docs.
  - [x] Spec committed verbatim to `docs/SPEC.md` (first commit).
  - [x] Environment checked: 4 CPU, 15 GB RAM, ~31 GB free disk. Python 3.13 locally, project pins 3.12 via uv. All data hosts reachable from this environment (no geoblock on reads).
  - [ ] `scripts/probe_endpoints.py` written and run; fixtures saved; DATA_NOTES filled in.
  - [ ] CI green on GitHub.

## Next

1. Finish Phase 0: probe every endpoint in SPEC §3, save trimmed fixtures to `tests/fixtures/`, document schemas/quirks in DATA_NOTES, push, confirm CI.
2. Phase 1: HTTP clients → Gamma market catalog → universe pass 1 (DuckDB over HF `order_filled`) → pass 2 (fills_norm + prices_5m) → API backfill → `reports/phase-1.md`.

## Open blockers

- None.

## Standing constraints (never stale)

- Paper trading only; no keys, no accounts, no geoblock circumvention (SPEC §0).
- Work happens on branch `claude/dreamy-gates-8wlal5`; `main` only moves by PR (see DECISIONS d-002).
- Disk allowance ~31 GB: never download the 127 GB HF dataset; stream via DuckDB `hf://` with partition pruning and projection.
