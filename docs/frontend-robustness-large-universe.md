---
Date: 2026-09-22
Branch context: feat/trigger-universe-scan
Scope: web/ (Next.js 15, `next dev`, basePath `/signals-app`)
Trigger: the 954-symbol, 8-timeframe local scan (engine_runs.id=107, 288 published)
Companion: docs/session-summary-2026-09-22-universe-scan-8-timeframes.md
---

## Progress

| Phase | Branch | PR | Scope | Status |
|---|---|---|---|---|
| 1 | `fix/universe-dev-robustness` | [#26](https://github.com/adamaslan/signals-app/pull/26) | §C — F11, F12, progress, stuck-run sweep, error boundaries, dev perf footer | **Shipped** (PR open, targets `main`) — F11 ✅ F12 ✅ progress ✅ stuck-run sweep ✅ error boundaries ✅ perf footer ✅ |
| 2 | `feat/universe-view-at-scale` | [#27](https://github.com/adamaslan/signals-app/pull/27) | A1 summary strip · A2 heatmap group/collapse/density · A3 filter/search/window | **Shipped** (PR open, stacked on #26 — base branch is `fix/universe-dev-robustness`, retarget to `main` after #26 merges) — A1 ✅ A2 ✅ A3 ✅ `universeView.ts` + 16 tests ✅ |
| 3 | `feat/deep-dive-8-slot-matrix` | [#28](https://github.com/adamaslan/signals-app/pull/28) | B1 fixed-slot matrix · B2 top date line | **Shipped** (PR open, stacked on #27 — base branch is `feat/universe-view-at-scale`, retarget to `main` after #26+#27 merge) — B1 ✅ B2 (top line) ✅ · B2 per-cell window context deliberately deferred (needs a new backend field, see PR body) |

Merge order per §4: **3 (dev robustness) → 1 (universe view) → 2 (deep dive)**
in this doc's own PR-split numbering — i.e. this progress table's **Phase 1
is the doc's "PR 3"**, **Phase 2 is the doc's "PR 1"**, and **Phase 3 is the
doc's "PR 2"**, chosen to merge in that order because PRs "1" and "3" both
touch `UniverseEditor.tsx` and "3" changes the data flow "1" renders from.
The three branches are stacked in dependency order (1 → 2 → 3 in this
table's numbering): each PR's base branch is the previous phase's branch,
not `main`, so retarget each to `main` as the one below it merges.

Running total against the §0 checklist (12 failure modes) once all three
PRs merge: **7/12 passing** — F11 F12 F1 F2 F3 F6 F8 fixed. This clears the
doc's stated 50% (6/12) target with room; the remaining 5 (F4 F5 F7 F9 F10)
are P1/P2 per §4 and out of scope for this 3-phase pass.

One deliberate scope note: **`TIMEFRAMES` in this codebase currently has 6
entries** (1D/5D/1M/3M/6M/1Y), not the 8 the doc's title describes — the
5Y/MAX extension is separate, in-progress work sitting uncommitted on
`feat/trigger-universe-scan` (see that branch's session-summary doc) and was
intentionally left out of these three PRs to keep them backend-independent.
Phase 3's B1 fix is written against `TIMEFRAMES.length` generically, so it
becomes an 8-slot matrix automatically once that expansion lands and merges
— no further frontend change needed.

---

# Frontend robustness plan: 950+ ticker scans and single-ticker deep dives

The 2026-09-22 local scan was the first time the frontend received a
full-universe result: 954 symbols, 288 published, 8 timeframes each, and
`ai_degraded: true` on every row. The UI was built and tested against baskets
of about 10–50 names. This doc lists where it breaks or stops being readable at
~950, and the changes that fix it.

This covers two views:

- **A. The universe view** (`/signals-app/universe/?id=N`): heatmap, table,
  timeline, and drift for a 950-name basket.
- **B. The deep-dive view** (`/signals-app/signal/?symbol=XOM`): one ticker
  across 8 timeframes.

Section C covers dev-only failures that make local runs flaky whatever the
basket size.

---

## 0. What "50% more robust" means here

"Robust" needs a number, or it can't be checked. Below are **12 failure modes**
found in the current code, each with a pass/fail check. Today the frontend
passes **0 of 12** at 950 tickers. **The target is ≥ 6 of 12 (50%)**, taken in
the P0 → P1 order in §4. The P0 items alone get there.

| # | Failure mode at 950+ | Where | Pass check |
|---|---|---|---|
| F1 | Heatmap renders ~954 × 56 px `<Link>` tiles in one unsorted wrap. The grid is about 20 screens tall and ~70% of it is grey "uncovered" tiles | [UniverseHeatmap.tsx:25-55](../web/src/components/UniverseHeatmap.tsx#L25-L55) | Whole basket's shape visible in ≤ 1.5 viewport heights at 1440×900 |
| F2 | The heatmap's `aiDegraded` corner dot sits on **every** tile, because every row in this run is degraded, so the dot tells you nothing | [UniverseHeatmap.tsx:68-73](../web/src/components/UniverseHeatmap.tsx#L68-L73) | Run-level degraded state is shown once, as a banner. Per-tile dot only when the run is mixed |
| F3 | Table renders all ~954 `<tr>` rows. It has no filter, no search, and no pagination | [UniverseTable.tsx:107](../web/src/components/UniverseTable.tsx#L107) | ≤ 100 rows in the DOM; filter by direction/coverage/freshness; ticker search |
| F4 | `runs` live query loads **every** past run with its full `results[]` array (954 × N runs) only to read `runs[0]`, `runs[1]` and the summaries | [UniverseEditor.tsx:62-73](../web/src/components/UniverseEditor.tsx#L62-L73) | Timeline reads summaries only; full results loaded for latest + previous run only |
| F5 | Timeline has **no date axis**: no x-tick labels, no y-ticks, no per-run tooltip. Runs are spaced by index, not time, so a gap of 3 weeks looks like a gap of 3 minutes | [UniverseTimeline.tsx:52-53](../web/src/components/UniverseTimeline.tsx#L52-L53) | Time-scaled x-axis with dated ticks; hover shows run date + counts |
| F6 | No top-of-page summary. The 288 / 666 / 4 split (published / uncovered / failed) exists in `run.summary` but is never shown as one strip | `UniverseRunSummary` in [db.ts:145](../web/src/lib/db.ts#L145) | One distribution strip above the views: bull/neutral/bear/uncovered/failed, clickable as filters |
| F7 | Freshness shows a relative age only (`3d`), never the absolute bar date or timezone. A mixed-age basket can't be told apart from a uniformly stale one | [UniverseTable.tsx](../web/src/components/UniverseTable.tsx), [freshness.ts](../web/src/lib/freshness.ts) | Tooltip / column shows `bar_ts` as `Sep 19, 16:00 ET`; run header shows the bar-date range across the basket |
| F8 | Deep-dive matrix **silently drops** timeframes with no signal (`if (!sig) return null`). 1D/5D vanish for every ticker because yfinance returns < 20 bars, and the layout shifts from one ticker to the next | [SignalMatrixRow.tsx:48](../web/src/components/SignalMatrixRow.tsx#L48) | Fixed 8-slot grid, Swing \| Long-term groups; empty slots render "n/a · insufficient bars" |
| F9 | Deep-dive has no per-timeframe date context. There's no way to see what window 5Y or MAX actually covered, or when each cell was computed | [signal/_client.tsx](../web/src/app/signal/_client.tsx) | Each matrix cell's tooltip / expanded panel shows window start → end and bar count |
| F10 | Deep-dive's "Not scanned yet" doesn't say *why*. For 666 of 954 names the cause is "scanned, gated", not "never scanned", and those two need different next steps | [signal/_client.tsx:66-79](../web/src/app/signal/_client.tsx#L66-L79) | Distinguishes gated / uncovered / failed, and links back to the universe row |
| F11 | Auto-run on first open probably **never fires in dev**. React StrictMode mounts, cleans up (`cancelled = true`) and re-mounts, and the `autoRanForId` ref makes the second mount return early. So the page sits empty until "Run basket" is clicked | [UniverseEditor.tsx:95-116](../web/src/components/UniverseEditor.tsx#L95-L116) | Opening a never-run universe under `next dev` produces a run with no click |
| F12 | Supabase reads have no timeout or abort. When a 5-chunk `.in()` loop stalls on a sleeping dev DB, the spinner never ends and navigating away doesn't cancel it | [api.ts:183-219](../web/src/lib/api.ts#L183-L219) | 15 s timeout per chunk; an error names the failing chunk; unmount aborts |

> F11 is inferred from reading the code, not reproduced. Next 15 App Router
> turns on StrictMode in dev by default and `next.config.ts` doesn't turn it
> off. Reproduce it first (§5, step 3) before fixing.

---

## A. The universe view at 950+ tickers

### A1. Summary strip first (F6), since everything else filters from it

A single horizontal stacked bar across the full width, built from
`latestRun.summary`:

```
[■■■■■ bull 131 ][■■ neutral 58 ][■■■■ bear 99 ][░░░░░░░░░░░░ uncovered 662 ][▪ failed 4 ]
 run 2026-09-22 14:07 ET · period 3M · bars dated Sep 19 → Sep 22 · AI synthesis off for 288/288
```

- Each segment is a button that sets the view filter (A3). Click "bear 99" and
  both the heatmap and the table show only those 99.
- Second line is the **date context**: when the run happened, and the
  min → max `barTs` across covered rows. If that range is wider than 3 calendar
  days, show it in amber. That means the basket mixes old and new bars.
- `AI synthesis off for 288/288` replaces 288 identical tile dots (F2). The
  per-tile dot comes back only when `0 < degraded < covered`.

New component: `web/src/components/UniverseSummaryStrip.tsx`. Pure function of
`UniverseRunSummary` plus a `barTs` min/max computed once in a `useMemo`.

### A2. Heatmap: group, compact and collapse (F1, F2)

The current heatmap shows each ticker in its own labelled tile. That works at
12 names. At 950 it just draws a long grey wall.

1. **Group by direction** in fixed order `strong_buy → buy → hold → sell →
   strong_sell`, then sort by confidence descending inside each group. Each
   group gets a small header with its count.
2. **Collapse uncovered and failed** into one line at the bottom:
   `662 not scanned · 4 failed [show]`. They carry no signal, so they
   shouldn't be drawn as 666 tiles.
3. **Density switch driven by size**:
   - `≤ 60` covered: current 56 px labelled tiles.
   - `61–400`: 28 px tiles, ticker label only on hover or focus.
   - `> 400`: 12 px cells in a CSS grid
     (`grid-template-columns: repeat(auto-fill, 12px)`), no text, native
     `title` tooltip. 288 cells at 12 px take about 3 rows at 1440 px.
4. **Swap `<Link>` for one delegated click handler** on the grid container
   (`data-ticker` on each cell → `router.push`). That's one handler instead of
   950 Next.js `Link` instances, each with its own prefetch observer.
   **Prefetch is the real cost in dev**: Next prefetches every visible `Link`,
   so 950 links in view produce a burst of 950 route fetches against
   `next dev`.
5. Keep **opacity = confidence**, but raise the floor from `0.35` to `0.5` at
   the 12 px density. Below that, cells stop being readable against
   `#0d0d1a`.

### A3. Table: filter, search and window (F3)

- A **filter bar** above the table (state shared with the heatmap and the
  summary strip):
  direction chips · `hide uncovered` (default **on** when > 100 rows) ·
  freshness (`fresh / stale / very-stale`) · min confidence slider · ticker
  search box (prefix match, uppercase).
- **Windowing.** Render at most 100 rows and add "Show next 100" at the
  bottom, plus a live count: `showing 100 of 288 (662 uncovered hidden)`.
  Pagination needs no new dependency. Only reach for `@tanstack/react-virtual`
  if paging turns out to be annoying in real use.
- Move sort + filter into one `useMemo` in a pure
  `web/src/lib/universeView.ts` (`filterAndSortResults(results, filter, sort)`)
  so it can be unit-tested in Vitest without rendering.
- **Freshness column** shows the relative age (`3d`) with the absolute
  timestamp in `title`. Format: `formatBarTs(ts)` → `Sep 19, 16:00 ET`, with a
  fixed `America/New_York` zone, because bar times are market times and not
  the viewer's local time (F7).
- **Sticky header** (`position: sticky; top: 0`) so the column labels stay
  visible while scrolling 100 rows.

### A4. Timeline with real dates (F4, F5)

- **Load summaries only.** Replace the single `runs` live query with two:
  - `latestTwo`: the newest 2 runs with full `results` (for heatmap, table,
    drift).
  - `runSummaries`: all runs mapped to
    `{ id, startedAt, universeRevision, summary }`. Dexie still reads each row
    in full, but the component tree no longer holds 954 × N result objects.
    The durable fix is to store summaries in their own table. That's a Dexie
    schema version bump, so it's listed separately as P2.
- **Time-scaled x-axis.** `x(startedAt)` over `[min, max]` instead of
  `x(index)`, so weekend and holiday gaps show as gaps.
- **Axis labels.** 4–6 dated x-ticks (`Sep 15`, `Sep 22`), y-ticks at
  0 / 50% / 100% of `maxCounted`, and a dot per run.
- **Hover tooltip per run:** `Sep 22 14:07 ET · 131▲ 58■ 99▼ · rev 4`.
- Add a **% toggle** (share of covered vs absolute count), because coverage
  rises from 12 to 288 as the scanner catches up, and in raw counts that looks
  like a bull run.
- Stays dependency-free inline SVG, matching the existing file's rule.

### A5. Drift view at scale

[UniverseDriftView.tsx](../web/src/components/UniverseDriftView.tsx) compares
two runs. At 950 it should show the **flips** only (direction changed), sorted
by `|Δconfidence|` and capped at 50, with a count of the rest. When the two
runs have different `universeRevision`, add one line saying so. That's the same
warning the timeline's purple markers carry.

---

## B. The deep-dive view (one ticker, 8 timeframes)

### B1. Fixed 8-slot matrix (F8)

Replace `TIMEFRAMES.map(… if (!sig) return null)` with a fixed layout:

```
 Swing                         │ Long-term
 1D    5D    1M    3M          │ 6M    1Y    5Y    MAX
 n/a   n/a   ▲     ▲           │ ■     ▼     ▼     ▲
 <20   <20   62%   71%         │ 48%   66%   58%   54%
 bars  bars
```

- Always 8 slots, grouped with `PERIOD_OPTIONS[].group`
  (`swing` / `long`) and a divider between the groups.
- An empty slot is a dashed cell, `n/a`. Its tooltip gives the reason when the
  backend supplies one, and says "no signal for this timeframe" when it
  doesn't.
- Confidence as a number under the arrow. Cell padding already scales with
  confidence, but padding is hard to compare across cells by eye.
- Fix the stale "6 timeframe cells" comment at
  [SignalMatrixRow.tsx:14](../web/src/components/SignalMatrixRow.tsx#L14) and
  [:44](../web/src/components/SignalMatrixRow.tsx#L44).
- **Mobile parity note:** the file says it is a port of
  `gcp3-mobile/components/SignalMatrixRow.tsx`. Changing the slot model here
  creates web/mobile drift. Log it on the parity page when this ships.

### B2. Date context per timeframe (F9)

For each timeframe, the deep dive should answer "what dates is this based on?"

- The expanded panel for a clicked cell gets a header line:
  `5Y · window 2021-09-22 → 2026-09-19 · 1,256 bars · computed Sep 22 14:07 ET`.
- **Backend dependency:** check whether the matrix entries already carry
  bar count and window dates before building this:
  ```bash
  cd ~/code/signals-app && grep -n "bar_count\|window_start\|n_bars\|bars" src/signals_app/schemas/signal_output.py src/signals_app/scoring/mtf.py
  ```
  If they don't, add `bars: int` and `window_start` / `window_end` (ISO dates)
  to each per-timeframe `Signal` in `mtf.py`. `signals.matrix` is `jsonb`, so
  no migration is needed. Until the fields exist, show only `computed <ts>`,
  from the top-level `created_at`.
- Top of the page: one **date line** under the ticker:
  `Bar Sep 19 16:00 ET · computed Sep 22 14:07 ET · Stale · 3d`. Today these
  are split between `SignalCard` and `FreshnessBadge`.

### B3. A price sparkline with signal markers

The deep dive has no picture of price, so a `▼ 5Y` verdict can't be checked
against anything. Add a small inline-SVG sparkline (1Y of daily closes) with a
marker on each of this device's `HistoryEntry` rows (colour = direction,
x = its timestamp).

- **Data source question, decide before building.** The frontend reads only
  Supabase, and no price table is exposed. Options, cheapest first:
  (a) store the last 252 closes in `signals.matrix["1Y"]` at scan time, which
  is simple but adds about 2 KB per row;
  (b) a `price_bars` view limited to `(ticker, date, close)`;
  (c) call `/api` on the local backend in dev only.
  **Recommendation: (a).** It works the same in the static export and in dev.
- Until the data exists, render the history timeline as dots on a date axis,
  with no price line. That still fixes the "no dates on the page" problem.

### B4. Explain empty results (F10)

`SignalNotFoundError` currently means any of three things. Split it:

| Case | How to detect | Message |
|---|---|---|
| Scanned, gated | row in `detector_hits` for the latest run, none in `signals` | "Scanned Sep 22, didn't clear the publication gate (\|confluence\| < 0.35)." |
| Uncovered | no `detector_hits` row | "Not in the scan universe. [Request coverage]" (reuses `requestCoverage`) |
| Failed | `detector_hits` / run log marks the symbol failed | "Last scan failed for this symbol: <reason>." |

`detector_hits` isn't browser-readable today (the backtest RPCs are
aggregate-only by design). The narrowest change is a
`symbol_scan_status(ticker, period)` RPC that returns
`{ last_scanned_at, outcome }` and nothing more. Until it exists, keep the
current message but change "Not scanned yet" to "No published signal", which
is true in all three cases.

### B5. Back-link to the basket

When the user arrives from a universe (`?from=universe:N`), show
`← Back to <universe name> (row 47 of 288)` with prev/next ticker buttons that
follow the current table sort and filter (pass them in sessionStorage, wrapped
in try/catch). This is the main deep-dive flow after a 950-name scan: step
through the 99 bearish names one at a time.

---

## C. Dev-local failures (any basket size)

| Item | Change |
|---|---|
| **F11** StrictMode auto-run | Set the `autoRanForId` ref only **after** the run starts, not before the async check, or drop the ref and dedupe on a Dexie `status: "running"` row for this universe. Test under `next dev`, not `next build`. |
| **F12** read timeouts | Add a `withTimeout(promise, ms, label)` helper in `api.ts`. Wrap each chunk's query at 15 s. The error names `chunk 3/5 (tickers AAPL…MSFT)`. Pass an `AbortSignal` through `runUniverse` so unmounting the editor cancels. Supabase-js supports `.abortSignal(signal)` on queries. |
| Progress for 5 chunks | `fetchUniverseSignals` takes an `onProgress(done, total)` callback. The button reads `Reading 600/954…` instead of spinning with no feedback. |
| Stuck `running` rows | A run interrupted by a hot reload stays `status: "running"` forever. On editor mount, mark runs older than 5 min still `running` as `failed: interrupted`. |
| Unbounded IndexedDB | Each 954-row run is roughly 150–250 KB. Keep full `results` for the newest 20 runs per universe and strip older ones to summary only (they still feed the timeline). |
| Error boundary | Wrap the heatmap, table, timeline and deep-dive matrix each in a small error boundary, so one bad row (e.g. an unexpected `divergence_pattern`) doesn't blank the whole page. |
| Dev-only perf readout | Behind `process.env.NODE_ENV === "development"`, a footer with `rows rendered · last read ms · chunks`. That gives the §0 checks a number to read. |

---

## 4. Order of work

| Priority | Items | Fails fixed | Running total |
|---|---|---|---|
| **P0** | A1 summary strip · A2 heatmap group/collapse/density · A3 filter + window · C/F11 auto-run | F1 F2 F3 F6 F11 | 5 / 12 |
| **P0** | B1 fixed 8-slot matrix | F8 | **6 / 12 → 50% target met** |
| P1 | A4 timeline dates + summary-only load · C/F12 timeouts + progress | F4 F5 F12 | 9 / 12 |
| P1 | A3 absolute bar timestamps · B2 top date line | F7 (+ partial F9) | 10 / 12 |
| P2 | B2 per-timeframe windows (backend field) · B4 gated/uncovered/failed RPC | F9 F10 | 12 / 12 |
| P2 | B3 sparkline · B5 prev/next · A5 drift flips · Dexie summary table · run pruning | polish | — |

Suggested split for PRs (keeps shared files disjoint):

1. `feat/universe-view-at-scale`: A1, A2, A3, `lib/universeView.ts` + tests.
   Touches `UniverseHeatmap`, `UniverseTable`, `UniverseEditor` (render section
   only), new `UniverseSummaryStrip`.
2. `feat/deep-dive-8-slot-matrix`: B1, B2 top line. Touches
   `SignalMatrixRow`, `signal/_client.tsx`, `SignalCard`.
3. `fix/universe-dev-robustness`: F11, F12, progress, stuck-run sweep.
   Touches `UniverseEditor` (effects only), `api.ts`, `universe.ts`.

PRs 1 and 3 both edit `UniverseEditor.tsx`, in different regions. Merge 3
first, because it changes the data flow that 1 renders from.

---

## 5. Verify: baseline now, re-check after each PR

**Step 1: preflight.** Right directory, dev server up, Supabase configured.

```bash
cd ~/code/signals-app/web && \
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/signals-app/ && \
  grep -cE '^NEXT_PUBLIC_SUPABASE_(URL|ANON_KEY)=.+' .env.local
```
Expect `200`, then `2`. If it prints `000`, start the server:

```bash
cd ~/code/signals-app/web && npm run dev
```

**Step 2: unit tests pass before any change.**

```bash
cd ~/code/signals-app/web && npm test
```
Expect all Vitest suites green (`freshness`, `stats`, `universe`).

**Step 3: reproduce F11 (StrictMode auto-run).** In the browser, create a new
universe, paste 20 tickers, open it, and don't click anything.

🖱 **Browser:** http://localhost:3000/signals-app/universe/

Expect today: no heatmap appears. Expect after the fix: a run appears within a
few seconds.

**Step 4: count DOM rows and tiles on a 950-name basket** (F1, F3). Open the
950-name universe in the browser, then paste into DevTools console:

```js
({ tiles: document.querySelectorAll('a[href*="/signal/?symbol="]').length,
   rows: document.querySelectorAll("tbody tr").length,
   pageHeightScreens: +(document.body.scrollHeight / innerHeight).toFixed(1) })
```
Baseline expectation: `tiles` ≈ 954 (heatmap view) or `rows` ≈ 954 (table
view), `pageHeightScreens` > 10. Target: `tiles` + `rows` ≤ 400 and
`pageHeightScreens` ≤ 3.

**Step 5: deep-dive slot count** (F8).

🖱 **Browser:** http://localhost:3000/signals-app/signal/?symbol=XOM&period=3mo

Then in DevTools console:

```js
document.querySelectorAll('[aria-label*="confidence"], [data-tf-slot]').length
```
Baseline: `6` (1D/5D missing). Target: `8` on every ticker.

**Step 6: e2e suite still green** after each PR.

```bash
cd ~/code/signals-app/web && npx playwright test e2e/universe.spec.ts e2e/dashboard.spec.ts
```

**Step 7: add the §0 checks as e2e assertions** so they don't regress. Create
`web/e2e/scale.spec.ts` seeding a 950-ticker universe through the same
Dexie path `universe.spec.ts` uses, and assert the step-4/5 targets. Run it:

```bash
cd ~/code/signals-app/web && npx playwright test e2e/scale.spec.ts
```

---

## Open questions (owner decisions)

- **B3 price data:** store closes in `signals.matrix` (recommended), add a
  view, or dev-only backend call?
- **B4 status RPC:** is exposing per-symbol `last_scanned_at` + outcome to the
  anon role acceptable? It exposes less than `latest_signals` already does,
  but it's a new read surface.
- **Density thresholds (60 / 400)** are starting guesses, not measurements.
  Adjust after looking at the real 288-name run.
