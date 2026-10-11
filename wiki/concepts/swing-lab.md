# Swing Lab — "When to Buy, When to Sell" as Queries

Generic building blocks for swing-trading studies on one ticker's own daily
history, plus a `signals swing …` CLI over them. Shipped on `feat/swing-lab`
(2026-10-10). Code: [`src/signals_app/swing/`](../../src/signals_app/swing/),
CLI: [`cli/swing.py`](../../src/signals_app/cli/swing.py).

## Why it exists

The first SNDK swing analysis (2026-10-10) was a one-off scratch script: a
grid of dip entries × take-profit/stop/max-hold exits, plus a "does a big
run-up mark a top?" table. Each new question meant editing the script. The
swing lab splits that script into composable pieces, so a new question is a
command line instead.

## The pieces

| Module | What it is |
|---|---|
| `triggers` | Pure `close → bool mask` conditions: `dip(w, pct)`, `runup(w, pct)`, `below_sma(n, pct)`, `above_sma(n, pct)`, combined with `all_of`/`any_of`, or parsed from `"dip(5,12) & below_sma(50) \| runup(10,40)"`. They use only the current and earlier bars, so there is no look-ahead. |
| `trades` | `simulate(close, mask, ExitRule)` takes non-overlapping trades: enter at the trigger close, exit at take-profit, stop or `max_hold`. `summarize` returns `TradeStats`: n, avg/median, win rate, worst/best, bars held, % per bar, compounded, first-half/second-half averages, and `robust`. |
| `forward` | `conditional_forward(close, mask, h)`: the forward return on condition bars compared with the all-bars baseline (`edge_pct`, `down_rate`). It answers the sell-side question with the same machinery as the buy side. |
| `grid` | `run_grid` (entries × exits) and `run_forward` (conditions × horizons) return DataFrames. `query(df, where, sort, top)` is a thin wrapper over `DataFrame.query`. |

`robust` means the average trade was positive in **both** halves of the
history, with at least 3 trades in each. It is a weak filter. Both halves can
fall inside the same bull run, as they did for SNDK, which rose about 43× over
the test window.

## CLI

```bash
signals swing grid SNDK --where "robust and n >= 12" --sort pct_per_bar --top 10
signals swing grid SNDK MU WDC --entry "dip(5,12)" --entry "dip(10,15) & below_sma(20)"
signals swing test SNDK --entry "dip(5,15)" --tp 5 --stop 8 --hold 10 --trades
signals swing forward SNDK --runup-windows 5,10 --runup-pcts 20,30,40 --horizons 5,10
signals swing levels SNDK --dips 5,12,15     # trigger prices from today's highs
```

Every command takes `--json`. The table commands also take `--csv`. Data comes
from `DataFetcher.fetch_daily_history`, so it uses whatever vendor order that
fetcher has (yfinance on `main`; Alpaca first once the Alpaca PR lands).

## Model limits (stated once, apply everywhere)

- Close-only. There are no intraday highs or lows, fees, slippage or gap
  modelling, so a real stop can fill worse than the reported `worst_pct`.
- In-sample on one ticker's history. The best grid row is the best *fit* to
  that history; it is not a forecast.
- Grid rows can be near-duplicates when a stop or hold limit never fires
  (identical stats). Filter them with `--where`, or read them as "this
  parameter didn't matter".

## Related

- [backtest-lab](backtest-lab.md): detector hit-rates, a different question
  (does a detector's label mean anything?).
- The dip-timing study and chain runner (`studies/dip.py`, `chains.py`) arrive
  in PR #47. The `swing_grid` chain step (PR #49) exposes this lab to chains and
  to holdem's `/api/thesis`; it takes structured params, never a query string.
