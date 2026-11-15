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
