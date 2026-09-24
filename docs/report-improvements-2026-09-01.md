# Universe Scan Report — Improvements (2026-09-01)

## Summary

Simplified the `scan_universe_report.py` HTML and Markdown output to focus on **actionable high-level data**, removing intermediate statistical tables that cluttered the report without adding decision-making value.

## What Changed

### Removed Sections

1. **"Why symbols were gated" table**
   - **What it showed:** Breakdown of gate rejection reasons (e.g., "570 symbols gated due to |confluence| < 0.35")
   - **Why removed:** This is a detailed filter audit, not actionable output. Gate logic is already documented in the code; readers don't need a frequency breakdown of why gating happened.

2. **"Category firing across the universe" table**
   - **What it showed:** Per-category statistics (symbols firing, fire rate %, total hits, bull/bear split)
   - **Why removed:** Too granular and intermediate. Individual signal categories firing are visible in the per-symbol detail section; universe-wide aggregates are engineering metrics, not trade signals.

3. **"Strongest bullish/bearish confluence" tables**
   - **What it showed:** Top N symbols ranked by confluence score with bias/action/signal count
   - **Why removed:** Redundant with the per-symbol detail section and the published symbols list. Duplicate rankings added no new insight.

### What Remains

- **Top-level summary**: Scanned/ok/published/gated counts + runtime
- **Bias & action distribution**: High-level view of bullish vs bearish, buy/sell/hold bias
- **Published symbols**: Clean list of symbols that cleared the gate (the primary actionable output)
- **Per-symbol detail**: Full confluence score, signals breakdown, category hits, top signals by conviction, and gate reasons for each symbol

## Impact

- **Cleaner visual flow:** Report goes straight from summary → bias distribution → published list → details
- **Focused intent:** What symbols passed? (published list) + Why? (per-symbol gate reasons)
- **Less noise:** Removed intermediate statistics that would only be useful for debugging the detector itself, not for trading decisions

## Files Modified

- `scripts/scan_universe_report.py`
  - HTML renderer: removed sections at lines 646–690
  - Markdown renderer: removed sections at lines 426–453

## How to Run

```bash
cd /Users/adamaslan/code/signals-app
mamba run -n signals-app python scripts/scan_universe_report.py --seed seed/universe_symbols.csv --format html
```

Output goes to `scans3/universe-scan_{YYYYmmdd-HHMMSS}.html` with the cleaner format.
