## Optimal Monthly Scanner — Usage & Integration Guide

**Written:** 2026-08-20 · **Status:** available as `scripts/scan_optimal_monthly.py`

This document explains the **Optimal Monthly Scanner**, a bolt-on enhancement to the `signals-app` engine that produces a ranked bull/bear candidate report for a **one-month horizon**. It complements — not replaces — the production `scan_universe.py` path.

---

## 1. What it is

`scan_optimal_monthly.py` runs the same **L1 → L4 pipeline** as `scan_universe.py` but adds three layers of refinement:

- **Rolling-window aggregation** — instead of scoring only the latest bar, it computes confluence over the **last 10 bars** and aggregates a trend-stable score.
- **State vs. transition weighting** — signals that represent fresh events (crosses, breakouts, divergences) get extra weight, while persistent state conditions (e.g., “price above cloud”) do not dominate.
- **Category diversity gate** — a symbol must show agreement across at least **4 distinct signal categories**, not just many signals from one category (e.g., all support/resistance proximity).

After the deterministic gate, it optionally runs **LLM synthesis only on the top candidates** (capped by `--max-llm-candidates`) and can add the **multi-timeframe matrix** for the final winners.

---

## 2. Where it fits in the app

```text
scan_optimal_monthly.py
        │
        ▼
  L1 fetch + L2 indicators  (same as scan_universe)
        │
        ▼
  L3 detect + L4 score      (same detectors, same ConfluenceRanker)
        │
        ├──► Rolling-window aggregation (10 bars)
        ├──► State/transition bonus
        ├──► Category diversity gate
        │
        ▼
  Deterministic gate        (data quality ≥ 0.6, ≥5 signals, ≥4 categories)
        │
        ▼
  Optional LLM synthesis    (top N candidates only)
        │
        ▼
  Optional MTF matrix       (top 10, if --matrix)
        │
        ▼
  Markdown report → docs/optimal_monthly_candidates_YYYYMMDD.md
  Optional Supabase persistence (signals, engine_runs)
```

It reuses the exact same imports and contracts as the production scanner:

- `detect_all_signals()` — the 18 detectors
- `ConfluenceRanker` — strength-weighted bull/bear vote
- `load_strength_hit_rates()` — calibration weights
- `SignalWriter` / `SupabaseWriter` — optional persistence
- `build_matrix_for_symbol()` — 5-timeframe matrix (opt-in)

---

## 3. How many signals are involved?

The optimal scanner **does not change the detection layer** — it still uses the same **18 detectors** and the same parameter grids as the production scanner. The theoretical maximum per ticker per bar remains:

| Metric | Value |
|--------|-------|
| Detectors | 18 |
| Max distinct signal firings per ticker per bar | **166** |
| Max per universe scan (latest bar only) | 166 × 954 = **158,364** |

However, the optimal scanner performs **rolling-window aggregation over the last 10 bars** of a 6-month daily series. This means it computes signals for every historical bar (via `scan_historical`), but only the **last 10 bars with signals** contribute to the final ranking.

**Per ticker, the optimal scanner processes:**

- **Theoretical maximum (one bar):** 166 signals
- **Rolling window (10 bars):** up to 1,660 raw firings per ticker (though average observed ~20/bar ⇒ ~200)
- **Across the 954-ticker universe:** the scanner evaluates roughly 125 daily bars per ticker, so total raw firings considered can reach **~20,000 per ticker** in the worst case, but the ranking uses only the last 10 bars.

**Gate threshold:**  
The deterministic gate requires **at least 5 total signals on the latest bar**, **data quality ≥ 0.6**, and **≥4 distinct categories** within the window. The final **confluence score** (or LLM confidence) ranks the candidates.

> In practice, signal counts per latest bar range from 5 to 74, with a typical average around 20.

---

## 4. Key differences from `scan_universe.py`

| Feature | `scan_universe` | `scan_optimal_monthly` |
|--------|----------------|------------------------|
| **Bar focus** | Latest bar only | Rolling 10-bar aggregate |
| **Signal weighting** | Strength only | Strength + transition bonus + category bonus |
| **Category diversity** | Not enforced | ≥4 distinct categories required |
| **LLM synthesis** | All gated symbols | Top N deterministic candidates only (cap) |
| **Multi-timeframe matrix** | Opt-in `--matrix` | Opt-in, but only for top 10 after LLM |
| **Output** | Supabase rows | Markdown report + optional Supabase |
| **Best for** | Daily production scan | Research, ranking, “what to trade next” |

---

## 5. Usage

### Basic dry-run (fast, no LLM, no writes)

```bash
python scripts/scan_optimal_monthly.py --dry-run
```

Scans the full 954-ticker universe, applies the deterministic gate, and writes a report to `docs/optimal_monthly_candidates_YYYYMMDD.md`. Top 50 bullish and 50 bearish candidates are listed.

### Full run with LLM synthesis (cost-controlled)

```bash
python scripts/scan_optimal_monthly.py
```

Runs the deterministic scan, then synthesizes **only the top 200 candidates** by window score. The final report ranks candidates by LLM confidence if available.

### Persist to Supabase

```bash
python scripts/scan_optimal_monthly.py --write-supabase
```

Writes the top candidates to `signals` and `engine_runs` using the same `SignalWriter` protocol.

### Include multi-timeframe matrix

```bash
python scripts/scan_optimal_monthly.py --matrix
```

After LLM synthesis, the top 10 candidates also get a 5-timeframe matrix (1D/5D/1M/3M/6M) appended to their record. This is 5× the LLM calls for those symbols — use only when analysis is needed.

### Direction filtering

```bash
python scripts/scan_optimal_monthly.py --direction bullish
python scripts/scan_optimal_monthly.py --direction bearish
```

By default `--direction both` produces two ranked lists. Use `bullish` or `bearish` to restrict the gate to one side.

### Sharding (for large runs)

```bash
python scripts/scan_optimal_monthly.py --shard 0/4
```

Same deterministic sharding as production: over the sorted universe, every `TOTAL`-th symbol starting at `INDEX`.

### Custom top N

```bash
python scripts/scan_optimal_monthly.py --top 30
```

List the top 30 of each side instead of the default 50.

### Skip calibration

```bash
python scripts/scan_optimal_monthly.py --skip-calibration
```

Reuse an existing `strength_hit_rates_21d.json` file. If none exists, proceed uncalibrated.

### Limit universe / custom calibration

```bash
python scripts/scan_optimal_monthly.py --limit 100 --calibration-symbols AAPL MSFT NVDA
```

Useful for testing with a small subset.

---

## 6. Output

The markdown report is written to:

```
docs/optimal_monthly_candidates_YYYYMMDD.md
```

It contains two sections:

- **Bullish Candidates** — ranked by confidence (or window score if no LLM)
- **Bearish Candidates** — ranked by confidence (or window score if no LLM)

Each row shows:

| Rank | Ticker | Confidence | Window Score | Bull | Bear | Signals | Categories | Transitions | Sector |
|------|--------|------------|--------------|------|------|---------|------------|-------------|--------|

`Window Score` is the deterministic aggregate used before LLM synthesis. `Categories` is the number of distinct signal categories present in the rolling window, and `Transitions` counts fresh crossover/breakout/divergence events.

---

## 7. Integration with calibration and Supabase

The scanner uses the **same calibration pipeline** as the production scanner:

1. It loads the **21-day calibration file** (`calibration/strength_hit_rates_21d.json`) if available.
2. If not found, it runs `run_calibration()` automatically on the provided symbol list.
3. The resulting strength→hit-rate table is passed to `ConfluenceRanker`, exactly as `scan_universe` does.

When `--write-supabase` is used, the candidates are written to the same tables:

- `engine_runs` — one row per scan, with `trigger`, `git_sha`, status
- `signals` — one row per candidate that cleared the gate and (optionally) LLM synthesis
- `detector_hits` — written for **all** scanned tickers before the gate, preserving the “what did we reject?” corpus

The report itself is **not** stored in Supabase; it is a local markdown file under `docs/`.

---

## 8. Example

```bash
# Dry-run the top 50 bull/bear candidates
python scripts/scan_optimal_monthly.py --dry-run

# Full run with LLM synthesis + Supabase persistence + matrix for top 10
python scripts/scan_optimal_monthly.py --write-supabase --matrix

# Restrict to bearish candidates only
python scripts/scan_optimal_monthly.py --direction bearish --top 30
```

The console output will show the ranked tables, followed by stats:

```
Stats: 950 passed gate, 200 LLM synth, 4 failed, 42.3s
Report: /Users/.../docs/optimal_monthly_candidates_20260820.md
```

---

## 9. Limitations & tuning

- **Rolling window size** is fixed at 10 bars; change `WINDOW_SIZE` in the script to adjust responsiveness vs. stability.
- **Transition detection** is heuristic — it looks for keywords (`CROSS`, `BREAKOUT`, `DIVERGENCE`, `SPIKE`, `TK`) in signal names. For exact transition flags, the detectors would need to expose their signal type explicitly.
- **Category diversity gate** requires ≥4 categories; lower the `MIN_CATEGORIES` constant if the universe produces too few candidates.
- **LLM cost cap** is controlled by `--max-llm-candidates` (default 200). Set it lower for cheaper runs.
- **Data quality threshold** is hardcoded at 0.6 in the optimal scanner (vs. 0.7 in production). Adjust in `scan_symbol()` if stricter gating is desired.

---

## 10. Relationship to the broader app

The optimal scanner is a **research and ranking tool**, not a replacement for the production workflow. It shares the same detectors, scoring, calibration, and persistence layers, but uses a different aggregation strategy to surface high-conviction candidates for a 1-month horizon.

Use it when you want a **curated list** of the strongest bullish and bearish setups across the universe, without paying LLM costs on weak or ambiguous signals. The production `scan_universe.py` remains the scheduled workhorse that populates Supabase for the dashboard.