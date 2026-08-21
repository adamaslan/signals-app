# Signal Rendering (Frontend)

How a `Signal` / `ConfluenceResult` becomes UI. Primary components:
[`SignalCard.tsx`](../../web/src/components/SignalCard.tsx) and
[`ConfluenceBar.tsx`](../../web/src/components/ConfluenceBar.tsx).

## `SignalCard` — the primary unit

One ticker, one synthesized direction. Confidence drives visual weight
directly rather than through a separate label:

```ts
const cardOpacity = 0.3 + 0.7 * signal.confidence;
const borderWidth = 1 + 4 * signal.confidence;   // px
```

A low-confidence call visibly recedes (fainter, thinner border) instead of
needing a "low confidence" badge to read as weak. Border color comes from
`SIGNAL_COLORS[signal.direction]` (`web/src/lib/types.ts`):

| Direction | Color |
|---|---|
| strong_buy | `#00C853` |
| buy | `#4CAF50` |
| hold | `#757575` |
| sell | `#EF5350` |
| strong_sell | `#D50000` |

`SIGNAL_LABELS[signal.direction]` supplies the human-readable heading text.

**AI-unavailable badge**: shown whenever `signal.ai_degraded` is true — an
orange `⚠ AI unavailable` pill. This is the direct UI consequence of the
backend's fallback chain (see
[concepts/llm-synthesis.md](llm-synthesis.md)): a rule-based fallback is
never presented as if it were LLM-reasoned.

**Evidence**: collapsed by default behind a "Show N evidence items" toggle
(`expanded` state), rendered by `EvidenceList` when opened.

Meta line shows `{signal.timeframe} · {signal.prompt_version}` — surfaces
the prompt version for auditability directly in the UI, not just in logs.

## `ConfluenceBar` — bull/bear/neutral + alignment

Two independent visualizations, both driven by props (not fetched
directly):

1. **Segmented bar** — `bullCount`/`bearCount`/`neutralCount` (derived as
   `total - bullCount - bearCount`) rendered as a single rounded bar split
   into green/amber/red segments by percentage, with per-segment counts
   below.
2. **Alignment bar** — `alignmentScore` (0–1) as a percentage-filled bar,
   colored `#00C853` green above 0.7, `#FFD740` amber between 0.4–0.7,
   `#D50000` red below 0.4. This is the direct visual home for
   `TimeframeMatrix.alignment_score` from
   [concepts/multi-timeframe.md](multi-timeframe.md) — how much the
   different timeframes agree with each other.

## Other display components (not yet detailed here)

- `CouncilPanel` / `SignalMatrixRow` — built to show the full multi-timeframe
  `TimeframeMatrix` (one row per timeframe), pairing with
  `divergence_pattern`/`divergence_interpretation`.
- `SignalLineageTree` / `SignalHistoryPanel` / `RecentRunsTable` — historical
  view over the local Dexie `history` table (see
  [architecture/frontend.md](../architecture/frontend.md#local-first-data-model)).
- `WatchlistPanel` / `WatchlistButton` — surfaces `lastSignal` per watched
  ticker from the Dexie `watchlist` table.

These are stubbed for expansion in this wiki — worth a follow-up pass once
their prop shapes are read in detail.
