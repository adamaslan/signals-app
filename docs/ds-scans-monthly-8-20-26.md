```markdown
# Comparison: `scan_optimal_monthly.py` vs `signals_engine_single.py`

**Date:** 2026-08-20  
**Context:** Two scripts were created to address the need for a ranked monthly candidate scanner with improved signal aggregation. This document compares their design, features, and intended use.

---

## 1. Overview

### `scan_optimal_monthly.py`
An extension of the modular **signals-app** codebase. It imports the existing pipeline layers (`detection.orchestrator`, `scoring.confluence`, `scoring.mtf`, etc.) and adds:
- Rolling‑window aggregation over 10 bars
- State‑vs‑transition signal weighting
- Category diversity gate
- Cost‑controlled LLM synthesis (top N by deterministic score)
- Optional multi‑timeframe matrix for top candidates
- Full Supabase persistence (same `SignalWriter` as production)

### `signals_engine_single.py`
A **self‑contained single‑file** implementation of the same conceptual pipeline. It reimplements fetching, indicator calculation, signal detection, confluence scoring, gating, and reporting from scratch, without importing any app modules. It is intended as a portable, educational, or quick‑analysis tool.

---

## 2. Shared Features

Both scripts implement the core **L1 → L5 pipeline**:

```text
fetch → indicators → detect → confluence score → publication gate → (optional) LLM synthesis → report
```

**Common capabilities:**

- **Rolling‑window aggregation** – uses the last 10 bars to compute a more stable score than a single latest bar.
- **State vs. transition weighting** – gives extra weight to fresh crossover/breakout/divergence events.
- **Category diversity gate** – requires at least 4 distinct signal categories before publishing.
- **Directional gating** – supports `bullish`, `bearish`, or `both` via `--direction`.
- **Top N reporting** – defaults to top 50 for each side (`--top`).
- **LLM cost control** – only the top deterministic candidates are sent to the LLM (`--max-llm-candidates`).
- **Markdown report** – written to `docs/` with separate bullish and bearish sections.
- **Sharding** – `--shard INDEX/TOTAL` to split the universe across multiple workers.
- **Concurrency** – uses `ThreadPoolExecutor` with bounded concurrency (default 4).

---

## 3. Key Differences

| Aspect | `scan_optimal_monthly.py` | `signals_engine_single.py` |
|--------|---------------------------|----------------------------|
| **Code organisation** | Modular, imports `signals_app` modules | Self‑contained, all logic in one file |
| **Detector coverage** | Full 18 detectors from app; up to **166 distinct signals/bar** | Simplified subset (~13 categories, fewer parameter combos) → fewer signals |
| **Historical scanning** | Uses `scan_historical()` (proper bar‑by‑bar scan with `min_lookback=200`) | Simple loop over last `WINDOW_SIZE` bars after 200‑bar warm‑up |
| **Calibration** | Loads/generates 21‑day calibration file, passes to `ConfluenceRanker` | Not implemented; uses static `STRENGTH_SCORES` only |
| **LLM synthesis** | Full `synthesize_single` – returns direction, confidence, **evidence/counter‑evidence**; optional MTF matrix | Simple `llm_synthesize` – only direction + confidence via JSON, no evidence or matrix |
| **Supabase persistence** | Full integration via `SignalWriter`: `engine_runs`, `signals`, `detector_hits` | `--write-supabase` flag exists but **not implemented** (placeholder) |
| **Signal weighting** | Uses calibrated strength hit‑rates if available + category/transition bonuses | Static strength scores + category/transition bonuses |
| **Report detail** | Includes sector, data quality, categories, transitions | Simpler table (ticker, confidence, score, bull/bear, signals, categories, transitions) |
| **Dependencies** | Requires the full `signals-app` package and environment | Only `yfinance`, `pandas`, `numpy`, `requests` |
| **Production readiness** | Tested as part of the app; follows existing patterns | Quick utility; not tested; error handling is basic |
| **Performance** | Optimised modules, same as production scan (~40s for 954 tickers) | Slower per symbol due to re‑computing indicators and detection from scratch |

---

## 4. Detailed Comparison

### 4.1 Detector Coverage & Signal Count

- **`scan_optimal_monthly.py`** uses the app's 18 detectors with all parameter grids (e.g., BB with 4 periods × 4 std‑devs = 48 checks). The theoretical maximum is **166 signals per ticker per bar**, though typical observed is 5–74. This gives a richer feature set for the confluence ranker.
- **`signals_engine_single.py`** implements a simplified version: MA cross (11 pairs), RSI (7 periods, only 30/70 thresholds), MACD (standard), Bollinger (20,2), Volume (basic spike/low, divergence), Price action (3% threshold), Range proximity (1% only), MA distance (10% threshold), S/R (pivot high/low), Stochastic, ADX, Ichimoku, OBV/CMF. Signal counts will be lower, which may affect the category diversity gate (some categories may not fire enough to reach ≥4 distinct categories on every bar).

### 4.2 Rolling Window Implementation

- **`scan_optimal_monthly.py`** uses the app's `scan_historical()` which loops over **all** historical bars (after 200‑bar warm‑up) and runs the full detector set. It then selects the last `WINDOW_SIZE` bars (default 10) to aggregate. This provides a true rolling window over the latest bars and allows future extensions like full historical analysis.
- **`signals_engine_single.py`** implements a custom loop starting from `max(200, len(df)-WINDOW_SIZE)` to the end. It does not scan the entire history; it only processes the last 10 bars (after ensuring at least 200 bars exist). The result is similar for the final score, but it lacks the full historical scan capability and is slightly less efficient if one wanted to extend to longer windows.

### 4.3 Calibration Integration

- **`scan_optimal_monthly.py`** leverages the app's calibration subsystem. It checks for a 21‑day calibration file (`calibration/strength_hit_rates_21d.json`), and if missing, runs `run_calibration()` on the provided symbols. The resulting hit‑rate table is passed to `ConfluenceRanker`, which adjusts the weight of each signal strength based on historical accuracy. This makes the scoring empirically grounded.
- **`signals_engine_single.py`** does not implement calibration. It uses the static `STRENGTH_SCORES` mapping (same as the uncalibrated fallback in the app). The code mentions `DEFAULT_CALIBRATION_PERIOD` but never uses it. For accurate one‑month predictions, calibration is important.

### 4.4 LLM Synthesis & Multi‑Timeframe Matrix

- **`scan_optimal_monthly.py`** uses the app's `synthesize_single` function, which returns a rich object including direction, confidence, evidence (supporting signals), and counter‑evidence (contradicting signals). It also supports the `--matrix` flag to compute the 5‑timeframe matrix for the top 10 candidates via `build_matrix_for_symbol`. This matrix is stored in the record and included in Supabase.
- **`signals_engine_single.py`** has a minimal `llm_synthesize` function that calls OpenRouter/Gemini and parses a JSON with `direction` and `confidence`. It does not produce evidence/counter‑evidence, and there is no multi‑timeframe matrix option. The LLM call is also run for **all** gated candidates (then confidence is cleared for those beyond the cap), which is less cost‑efficient than the optimal script's approach of only synthesizing the top N after sorting.

### 4.5 Supabase Integration

- **`scan_optimal_monthly.py`** fully implements Supabase persistence when `--write-supabase` is passed. It uses the same `SignalWriter` protocol, writing `engine_runs`, `signals`, and `detector_hits` (for all scanned symbols) exactly like the production scanner. This enables the dashboard and calibration feedback loop.
- **`signals_engine_single.py`** accepts `--write-supabase` but does **not** implement it. The flag is parsed but ignored (the code does not import or use any Supabase client). It is a placeholder for potential future addition.

### 4.6 Report Content

Both write a markdown report, but the columns differ:

**`scan_optimal_monthly.py`** (sample columns):

| Rank | Ticker | Confidence | Window Score | Bull | Bear | Signals | Categories | Transitions | Sector |
|------|--------|------------|--------------|------|------|---------|------------|-------------|--------|

**`signals_engine_single.py`** (sample columns):

| Rank | Ticker | Confidence | Window Score | Bull | Bear | Signals | Categories | Transitions |
|------|--------|------------|--------------|------|------|---------|------------|-------------|

The optimal script includes sector (loaded from the seed CSV) and also stores data quality in the candidate object (though not shown in the table). The single‑file version omits sector and data quality columns.

---

## 5. Use Case Recommendations

### Use `scan_optimal_monthly.py` when:
- You are working inside the **signals-app repository**.
- You need the **full 18‑detector coverage** and calibrated scoring.
- You plan to **persist results to Supabase** for the dashboard or calibration.
- You want the **rich LLM output** (evidence, counter‑evidence) and optional multi‑timeframe matrix.
- You need **production‑grade reliability** (tested, modular, follows existing patterns).

### Use `signals_engine_single.py` when:
- You need a **quick, portable** script without installing the entire app.
- You are on a machine with only `yfinance`/`pandas` and want to run a limited analysis.
- You are **learning** the pipeline or want a self‑contained reference implementation.
- You don’t require calibrated weights or Supabase integration.
- You want to customise the detectors quickly without modifying the app’s module structure.

---

## 6. Conclusion

Both scripts address the same problem — producing a ranked list of bullish/bearish candidates for a one‑month horizon using confluence scoring and rolling‑window aggregation. The key difference is **production readiness vs. portability**:

- `scan_optimal_monthly.py` is the **preferred tool** within the signals-app ecosystem, fully integrated with calibration, Supabase, and the existing modular detectors.
- `signals_engine_single.py` is a **standalone demonstration** that sacrifices detector breadth and calibration for simplicity and self‑containment.

For most users of the signals-app, `scan_optimal_monthly.py` is the recommended script. The single‑file version is useful for quick experiments, education, or environments where the full app is unavailable.
```