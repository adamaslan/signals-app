# Manual TODO

This file holds only items a code change alone cannot resolve, or a merge
conflict that needs human judgment rather than an automated rebase. Append
only — never rewrite or re-sort existing entries.

### 1D/5D matrix columns lose all data under SA1's fix — needs a product decision
- **From**: PR #41 review (CodeRabbit), `src/signals_app/scanner.py:210`
- **Blocked on**: SA1 fixed the original bug (all 5 matrix columns silently
  collapsing to the same widened `"1y"` fetch, so 1D/5D/1M/3M/6M always
  returned identical results). But `widen_for_indicators=False` means the
  `"1D"`/`"5D"` periods fetch ~1 and ~5 daily bars respectively — never
  enough to clear the `< 20` bar floor in both `build_matrix_for_symbol`
  (scanner.py:211) and `score_single_timeframe` (scoring/mtf.py:148), so
  those two columns are now *always* dropped instead of occasionally missing
  from real data gaps. This trades "wrong" (identical columns) for "silently
  incomplete" (2 of 5 columns gone), which is safer but still not right.
  Fixing it correctly requires deciding what "1D"/"5D" are actually supposed
  to mean when every bar interval is daily (per FIBONACCI.md §12.1: "the
  period settings change how much daily history is used, not the bar
  interval") — e.g. is the "1D" column meant to show today's fully-warmed-up
  signal state (which, once indicators are warmed, is identical to every
  other column's "today" state — the same defect SA1 fixed, one level up),
  or a snapshot of the composite score as it stood 1 trading day ago
  (needing full historical warm-up ending at that earlier bar, not a
  1-bar-only fetch)? Those are different product semantics, not different
  implementations of the same idea, and the wrong guess would ship
  materially different trading-relevant scoring behavior.
- **Why it can't be code**: this is a design decision about what the 5-column
  matrix is supposed to represent, not a bug with one correct fix. CodeRabbit
  flagged it `Major`/`Heavy lift` for the same reason — the two remedies it
  offered ("provide separate indicator warm-up while preserving the window"
  vs "support degraded short-frame scoring") depend on which semantics is
  intended.
- **Unblocks**: SA1 (FIBONACCI.md §13.1) counting as fully done, and the
  5-column matrix actually showing 5 columns again.
- **Added**: 2026-09-28
