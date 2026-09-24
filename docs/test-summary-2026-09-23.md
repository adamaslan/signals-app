# Test run summary — 2026-09-23

**Branch:** `feat/trigger-universe-scan` (working tree dirty; see §5)
**Env:** mamba `signals-app` (`/opt/homebrew/Caskroom/miniforge/base/envs/signals-app`) for Python, local `node_modules` for web
**Scope:** full Python suite, web unit tests, web typecheck + production build, plus the two advisory linters CI runs

---

## 1. Headline

| Gate | Command | Result |
|---|---|---|
| Python tests | `python -m pytest -q` | ✅ **147 passed** in 21s |
| Web unit tests | `npm test` (vitest) | ✅ **40 passed** across 3 files |
| Web typecheck | `npx tsc --noEmit` | ✅ **0 errors** |
| Web build | `npx next build` | ✅ compiled, linted, 7/7 static pages, 3/3 exported |
| ruff (advisory in CI) | `ruff check .` | ⚠️ 237 findings |
| mypy (advisory in CI) | `mypy src/signals_app` | ⚠️ 69 findings |

Everything **blocking** is green. `ci.yml` runs ruff and mypy with `|| echo "::warning::…"`, so only `pytest` can fail the build.

---

## 2. One test was failing, and it was stale

`tests/test_scan_universe_report.py::TestRenderers::test_markdown_has_sections_and_symbols` failed on the first run:

```
AssertionError: assert '## Category firing across the universe' in '# Universe Signal Scan — …'
```

**Not a regression.** The assertion has been failing since the report script landed in `6c91a62` (#23):

- the heading is absent from `scripts/scan_universe_report.py` **on `HEAD`**, not just in the working tree;
- `docs/report-improvements-2026-09-01.md` §2 records the removal as deliberate — the universe-wide *"Category firing"* table was cut from **both** renderers as "too granular and intermediate", along with the gate-reason breakdown and the strongest-confluence tables;
- the test was simply never updated to match.

**Fix applied** — inverted the assertion and left the reasoning in place, so the next reader doesn't re-add the section:

```python
# NB: the universe-wide "Category firing" table was deliberately removed
# from both renderers (docs/report-improvements-2026-09-01.md §2);
# category_stats is still computed and covered by
# TestAggregate::test_category_stats_cover_every_category.
assert "## Category firing across the universe" not in md
```

The underlying data is still tested: `category_stats` is computed on `UniverseReport` and asserted by `TestAggregate::test_category_stats_cover_every_category` (every category present, fire-rate maths correct). Only the *rendering* of it is gone, which is what the doc intended.

**Loose end worth a decision, not a fix:** `category_stats` is now computed on every scan and rendered nowhere. It is either dead weight in the aggregate, or the §2.5 per-detector report card (`docs/scoring-2x-plan.md`) is its real consumer. Leaving it as-is is fine; deleting it would cost the report card its head start.

---

## 3. Web

`npx tsc --noEmit` initially reported two errors:

```
.next/types/app/admin/page.ts(2,24): error TS2307: Cannot find module '../../../../src/app/admin/page.js'
```

These are **stale generated artifacts**, not source errors — `src/app/admin/` does not exist, and `tsconfig.json` has `.next/types/**/*.ts` in `include`, so a deleted route left orphaned type stubs behind. `next build` regenerated `.next/types`, and the re-run of `tsc --noEmit` is clean at 0 errors.

Build output, all routes static:

| Route | Size | First Load JS |
|---|---:|---:|
| `/` | 3.85 kB | 142 kB |
| `/settings` | 5.75 kB | 201 kB |
| `/signal` | 9.49 kB | 213 kB |
| `/universe` | 11.3 kB | 215 kB |

Two non-fatal build warnings, both expected under `output: export`: `rewrites` are not applied when exporting. Worth knowing if anything is relying on a rewrite in the static build — nothing in this run was.

---

## 4. Lint drift (advisory, but it has grown)

`ci.yml:33` records the baseline in a comment: *"121 ruff findings and 38 mypy findings predate…"*. Today:

| Linter | Baseline in `ci.yml` | Now | Change |
|---|---:|---:|---|
| ruff | 121 | **237** | +116 |
| mypy | 38 | **69** | +31 |

Where they sit:

| ruff, by tree | count | | ruff, by rule | count |
|---|---:|---|---|---:|
| `scripts/` | 171 | | `E501` line too long | 114 |
| `src/` | 62 | | `E402` import not at top | 42 |
| `tests/` | 4 | | `F401` unused import | 35 |
| | | | `I001` unsorted imports | 14 |

| mypy, by file | count |
|---|---:|
| `mcp/server.py` | 29 |
| `detection/trend.py` | 13 |
| `synthesis/mtf_llm.py` | 5 |
| `scoring/mtf.py` | 4 |
| everything else (19 files) | 18 |

Two things stand out:

1. **`scripts/` carries 72% of the ruff findings** and is mostly `E501`/`E402`/`F401` — cosmetic, and `67 are auto-fixable` with `ruff check --fix`. Cheap to clear if anyone wants the number down.
2. **`mcp/server.py` carries 42% of the mypy findings**, nearly all `untyped-decorator` from the MCP `@server.tool()` decorators. That is a single upstream-typing issue, not 29 independent defects.

Neither blocks. Both are noted because the comment in `ci.yml` now understates reality by roughly 2×, and a stale baseline stops being a useful tripwire.

---

## 5. What was not tested

- **Nothing was run against live market data.** `service.backtest` still defaults to `DEFAULT_PERIOD` (63 bars), below the 205-bar warmup, so a default call errors — a known item in `docs/scoring-2x-plan.md` §0.3.
- **Playwright e2e (`npm run test:e2e`) was not run** — it needs a running server, and nothing in this change touches routing or interaction.
- **The working tree is dirty** (9 modified files, 11 untracked docs). These results describe the working tree, not `HEAD`. In particular `config.py`, `scanner.py`, `scoring/mtf.py` and `schemas/signal_output.py` carry uncommitted edits from the 8-timeframe work, and the loosened publication gate (0.15 / 2 signals) is part of what the Python suite just validated.
- **`docs/scoring-2x-plan.md` §2.5 is a specification, not code.** None of its targets (C1–C9, F1–F11) are measured by anything that ran here — that is the P0 harness, which does not exist yet.

---

## 6. Commands, to reproduce

**Step 1 — Python suite.**

```bash
cd /Users/adamaslan/code/signals-app
/opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -m pytest -q
```
Expect: `147 passed`.

**Step 2 — web unit tests.**

```bash
cd /Users/adamaslan/code/signals-app/web && npm test
```
Expect: `Test Files 3 passed (3)`, `Tests 40 passed (40)`.

**Step 3 — web typecheck and build.**

```bash
cd /Users/adamaslan/code/signals-app/web && npx next build && npx tsc --noEmit
```
Expect: `✓ Exporting (3/3)` then no tsc output. Run `next build` **before** `tsc` — `tsc` alone can trip over stale `.next/types` stubs (§3).

**Step 4 — advisory linters (non-blocking).**

```bash
cd /Users/adamaslan/code/signals-app
/opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -m ruff check .
/opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -m mypy src/signals_app
```
Expect: 237 and 69 findings respectively, as of this run.

**Step 5 — clear the cheap ruff findings, if wanted.**

```bash
cd /Users/adamaslan/code/signals-app
/opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -m ruff check . --fix
/opt/homebrew/Caskroom/miniforge/base/envs/signals-app/bin/python -m pytest -q
```
Expect: 67 findings removed, then `147 passed` again. Re-run the tests — `F401` removals can break a module that relied on a re-export.
