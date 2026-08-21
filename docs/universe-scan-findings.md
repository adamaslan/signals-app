# Full-Universe Scan — Measured Findings

**Measured:** 2026-08-20 · **Branch:** `feat/phase10-multi-timeframe-matrix`
**Method:** `scripts/scan_universe.py --seed seed/universe_symbols.csv --dry-run`
**Environment:** mamba env `signals-app`, local machine, no network throttling

Every number in this document was produced by running the command shown next to
it on the date above. Nothing here is copied forward from an earlier doc or
estimated. Where a number is *derived* rather than measured, it says so.

---

## Outline

1. [Headline numbers](#1-headline-numbers)
2. [The single-process run reproduces the sharded run exactly](#2-the-single-process-run-reproduces-the-sharded-run-exactly)
3. [What the publication gate actually rejects](#3-what-the-publication-gate-actually-rejects)
4. [Pass rate by asset type](#4-pass-rate-by-asset-type)
5. [Pass rate by sector — the one non-uniform axis](#5-pass-rate-by-sector--the-one-non-uniform-axis)
6. [The 4 hard failures](#6-the-4-hard-failures)
7. [Cost implications](#7-cost-implications)
8. [Timing implications for the sharded workflow](#8-timing-implications-for-the-sharded-workflow)
9. [What is still unmeasured](#9-what-is-still-unmeasured)

---

## 1. Headline numbers

| Metric | Value |
|---|---|
| Symbols in `seed/universe_symbols.csv` | **954** unique (955 lines incl. header) |
| Scanned successfully | **950** |
| Hard failures | **4** |
| **Cleared the publication gate** | **403** |
| Gated (scanned fine, not publishable) | **547** |
| Publication rate | **42.2 %** of the universe |
| Full-universe dry-run wall time | **43.8 s** |

**403 is the exact LLM call count for a full production run.** The publication
gate runs *before* synthesis by design (`scan_one_symbol` returns
`reason="gated"` before it ever imports `synthesize_single`), so gated symbols
cost zero LLM tokens.

---

## 2. The single-process run reproduces the sharded run exactly

The four Actions shards and one local process agree to the symbol:

| Shard | Symbols | Published |
|---|---|---|
| `0/4` | 239 | 115 |
| `1/4` | 239 | 96 |
| `2/4` | 238 | 98 |
| `3/4` | 238 | 94 |
| **Σ sharded** | **954** | **403** |
| **Unsharded, one process** | **954** | **403** |

This is the useful operational finding: **`--shard` is optional.** It exists for
GitHub Actions' job-time cap and failure isolation, not for correctness. Locally,
omit it.

The agreement is not a coincidence — `apply_shard()` selects
`i % shard_total == shard_index` over an *already-sorted* symbol list, so the
shards are a strict partition. Three independent runs produced 403 each time,
which also confirms the gate is deterministic given a fixed market close.

---

## 3. What the publication gate actually rejects

From `src/signals_app/config.py`:

| Constant | Value | Meaning |
|---|---|---|
| `PUBLISH_MIN_DATA_QUALITY` | `0.7` | Reject stale/gappy/thin data |
| `PUBLISH_MIN_SIGNALS` | `3` | Need ≥3 detectors firing at all |
| `PUBLISH_MIN_CONFLUENCE_SCORE` | `0.35` | `= CONFLUENCE_BUY_THRESHOLD` — HOLD-territory carries no information |
| `DEFAULT_PERIOD` | `"3mo"` | The scan window |

A 42 % pass rate is the designed behavior, not a leak. The plan's stated
intent — *"Most ticker-days should fail this gate. That is the point: an engine
that always emits a direction carries no information"* — is satisfied, though
42 % sits at the permissive end of "most". The `abs()` on the confluence check
means the gate is direction-neutral: a strong bearish reading publishes just as
readily as a strong bullish one.

**Sanity check from TODO §2 passes.** The failure mode to watch for was a total
approaching 954, which would mean the gate wasn't gating. 403 is comfortably
clear of that.

---

## 4. Pass rate by asset type

| Asset type | In universe | Published | Rate |
|---|---:|---:|---:|
| Equity | 776 | 326 | 42.0 % |
| ETF | 173 | 74 | 42.8 % |
| Crypto | 4 | 3 | 75.0 % |
| Fund | 1 | 0 | 0.0 % |

**Equities and ETFs pass at an almost identical rate.** That is worth noting
because ETFs are baskets — they should be *smoother* and therefore fire fewer
momentum/divergence detectors. They don't. Either the detector mix is dominated
by trend features that survive basket averaging, or the leveraged/inverse ETFs
(87 of the 173) are dragging the ETF rate up with their amplified moves. The
sector table below supports the second reading.

Crypto and Fund have `n ≤ 4`. Ignore those rates.

---

## 5. Pass rate by sector — the one non-uniform axis

Sectors with n ≥ 15, by universe count:

| Sector | In universe | Published | Rate |
|---|---:|---:|---:|
| Utilities | 32 | 24 | **75.0 %** |
| Consumer Defensive | 46 | 26 | **56.5 %** |
| Real Estate | 34 | 19 | **55.9 %** |
| Leveraged & Inverse | 87 | 38 | 43.7 % |
| Technology | 163 | 67 | 41.1 % |
| Financial Services | 105 | 43 | 41.0 % |
| Index / Sector / Thematic | 72 | 29 | 40.3 % |
| Healthcare | 76 | 30 | 39.5 % |
| Basic Materials | 43 | 17 | 39.5 % |
| Communication Services | 38 | 15 | 39.5 % |
| Consumer Cyclical | 91 | 34 | 37.4 % |
| Industrials | 106 | 39 | 36.8 % |
| Energy | 36 | 9 | **25.0 %** |

**This is the most informative table in the document.** Asset type barely moves
the rate; sector moves it by 3x (Energy 25 % → Utilities 75 %).

That spread is evidence the gate is measuring something real. If the gate were
structurally biased — a detector bug, a threshold artifact — you would expect it
to fire uniformly across sectors, because the detectors are sector-blind. Instead
the rate-defensive sectors (Utilities, Consumer Defensive, Real Estate) cluster
high and the cyclical/commodity sectors cluster low, which is a coherent
market-regime story for a single 3-month window.

**Caveat, and it is a real one:** this is *one* snapshot of *one* period on *one*
day. A single day's sector rotation fully explains this pattern. Do not treat it
as a stable property of the engine until the same breakdown has been run across
several sessions. The right follow-up is to re-run this table weekly and see
whether Utilities stays at the top — if it does across regimes, that's a detector
bias worth investigating; if it moves with the market, the gate is working.

---

## 6. The 4 hard failures

| Ticker | Reason |
|---|---|
| `EVGOW` | `insufficient_bars` (<20 bars in 3mo) |
| `FM` | yfinance returned empty data |
| `MIDZ` | yfinance returned empty data |
| `TBHC` | yfinance returned empty data |

0.4 % failure rate. All four are data-availability problems upstream in
yfinance, not engine bugs — `EVGOW` is a warrant, and the other three are
delisted or thinly-traded tickers the seed CSV still lists.

These are correctly *tallied* rather than fatal: `scan_one_symbol` catches every
exception and returns a `SymbolResult`, so one bad ticker never aborts the run.
At this rate `scan_universe`'s status logic records `status="ok"` only at exactly
zero failures, `"partial"` below 20 % — so a real run will land on `"partial"`.
That is expected and not an alarm.

**Suggested cleanup (low priority):** prune the 4 from `seed/universe_symbols.csv`,
or accept a permanent `"partial"` status on every run. Pruning is cleaner — a
status field that is always `"partial"` stops carrying signal.

---

## 7. Cost implications

- **403 LLM calls** per full-universe run, one call per published symbol via
  `synthesize_single`.
- Active provider is OpenRouter when `OPENROUTER_API_KEY` is set, defaulting to
  model `google/gemini-2.0-flash-001` (`config.py:230`); it takes priority over
  Gemini. With no key, `llm_provider` is `"none"`.
- **Token counts per call are still unmeasured** — TODO §2's second checkbox. The
  call count is now exact; the per-call cost is not. One live single-symbol run
  closes that gap.

**`--matrix` at full-universe scale is the cost cliff.** It computes 5 timeframes
per gated symbol, each with its own LLM call: **403 × up to 5 ≈ 2,000 calls**, a
5x multiplier on both fetches and spend. It is deliberately unavailable on the
`full_universe` workflow path for exactly this reason. Running it locally
bypasses that guard without removing the cost — treat P3 §9 as a priced decision,
now that the 403 base number exists.

---

## 8. Timing implications for the sharded workflow

The local full-universe dry run took **43.8 s** for 954 symbols at
`--max-concurrent 4`, i.e. **~46 ms/symbol**, CPU-bound at only 47 % — it is
dominated by yfinance network waits.

That figure sets a floor, not an estimate, for the real run:

- A dry run makes **zero** LLM calls. The real run adds 403 sequential-per-worker
  synthesis calls with a 15 s timeout each (`OPENROUTER_TIMEOUT_SECONDS`).
- LLM latency, not fetching, will dominate real wall time.
- The 90-minute `timeout-minutes` cap on the sharded job therefore cannot be
  validated from this measurement. TODO §3's "record per-shard wall time"
  remains genuinely unmeasured.

What this *does* establish: the fetch/compute half of the pipeline is nowhere
near the cap. If a shard runs long, LLM latency is the cause, and the fix is
shard count (`[0..7]`) or concurrency — not detector optimization.

---

## 9. What is still unmeasured

Explicitly listing these so the measured numbers above aren't over-read:

- [ ] **Per-call token counts / actual dollar cost.** Needs one live call.
- [ ] **Real (non-dry-run) wall time**, per shard and total.
- [ ] **Whether the sector spread in §5 is stable across days.** One snapshot.
- [ ] **Supabase write throughput** at 403 rows — dry run performs no writes, so
      `ensure_symbol` / `write_detector_hits` / `write_signal` are untested at
      this volume. Note that `write_detector_hits` runs for *every* scanned
      symbol, not just published ones — that is 950 writes, not 403.
- [ ] **`--matrix` cost**, measured rather than derived from the 5x multiplier.

---

## Reproducing this document

```bash
source /opt/homebrew/Caskroom/miniforge/base/bin/activate signals-app

# headline numbers (§1, §2, §6)
python scripts/scan_universe.py --seed seed/universe_symbols.csv --dry-run

# per-shard, to confirm the partition (§2)
for s in 0 1 2 3; do
  python scripts/scan_universe.py --seed seed/universe_symbols.csv \
    --shard $s/4 --dry-run 2>&1 | grep '^Scanned'
done
```

The §4/§5 breakdowns join the printed `published:` list against
`seed/universe_symbols.csv` on the `asset_type` and `sector_group` columns.

See also: [TODO.md](TODO.md) · [backend-state-and-supabase-plan.md](backend-state-and-supabase-plan.md)
