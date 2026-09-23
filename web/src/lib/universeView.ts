/**
 * Pure filter/sort/group logic shared by the universe heatmap, table, and
 * summary strip at scale (§A of docs/frontend-robustness-large-universe.md).
 * Kept dependency-free and rendering-free so it can be unit-tested without
 * mounting a component — see universeView.test.ts.
 */
import type { UniverseRunResult, UniverseRunSummary } from "./db";
import { classifyFreshness, type Freshness } from "./freshness";
import type { SignalDirection } from "./types";

/** Fixed direction order used everywhere the basket is grouped by strength —
 * strongest bullish to strongest bearish. */
export const DIRECTION_ORDER: SignalDirection[] = [
  "strong_buy",
  "buy",
  "hold",
  "sell",
  "strong_sell",
];

export type ResultStatus = "covered" | "uncovered" | "failed";

export function resultStatus(r: UniverseRunResult): ResultStatus {
  if (r.error === "uncovered") return "uncovered";
  if (r.error != null) return "failed";
  return "covered";
}

export interface UniverseViewFilter {
  /** Empty = no direction restriction. */
  directions: Set<SignalDirection>;
  /** Hides uncovered rows from the table/heatmap (still counted in the
   * summary strip). Doc default: on when > 100 rows. */
  hideUncovered: boolean;
  /** Empty = no freshness restriction. */
  freshness: Set<Freshness>;
  /** 0–1; rows below this confidence are excluded. */
  minConfidence: number;
  /** Prefix match, case-insensitive (compared uppercased). */
  tickerSearch: string;
}

export function defaultFilter(resultCount: number): UniverseViewFilter {
  return {
    directions: new Set(),
    hideUncovered: resultCount > 100,
    freshness: new Set(),
    minConfidence: 0,
    tickerSearch: "",
  };
}

function matchesFilter(r: UniverseRunResult, f: UniverseViewFilter): boolean {
  const status = resultStatus(r);
  if (f.hideUncovered && status === "uncovered") return false;
  if (f.directions.size > 0) {
    if (!r.signal || !f.directions.has(r.signal)) return false;
  }
  if (f.freshness.size > 0) {
    const level = classifyFreshness(r.barTs).level;
    if (!f.freshness.has(level)) return false;
  }
  if (f.minConfidence > 0 && (r.confidence ?? 0) < f.minConfidence) {
    return false;
  }
  if (f.tickerSearch.trim()) {
    const needle = f.tickerSearch.trim().toUpperCase();
    if (!r.ticker.toUpperCase().startsWith(needle)) return false;
  }
  return true;
}

export type UniverseSortKey =
  | "ticker"
  | "signal"
  | "confidence"
  | "confluence"
  | "dataQuality"
  | "alignment"
  | "freshness";

const DIR_RANK: Record<SignalDirection, number> = {
  strong_sell: -2,
  sell: -1,
  hold: 0,
  buy: 1,
  strong_buy: 2,
};

function sortValue(r: UniverseRunResult, key: UniverseSortKey): number | string {
  switch (key) {
    case "ticker":
      return r.ticker;
    case "signal":
      return r.signal ? DIR_RANK[r.signal] : -99;
    case "confidence":
      return r.confidence ?? -1;
    case "confluence":
      return r.confluenceScore ?? -1;
    case "dataQuality":
      return r.dataQuality ?? -1;
    case "alignment":
      return r.alignmentScore ?? -1;
    case "freshness":
      return r.barTs ?? -1;
  }
}

export interface FilterAndSortOpts {
  sortKey?: UniverseSortKey;
  sortAsc?: boolean;
}

/** Filter, then (optionally) sort. Sorting is stable and off by default —
 * the heatmap groups instead of sorting flat; the table passes sortKey. */
export function filterAndSortResults(
  results: UniverseRunResult[],
  filter: UniverseViewFilter,
  opts: FilterAndSortOpts = {},
): UniverseRunResult[] {
  const filtered = results.filter((r) => matchesFilter(r, filter));
  if (!opts.sortKey) return filtered;
  const key = opts.sortKey;
  const asc = opts.sortAsc ?? false;
  return [...filtered].sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    const cmp =
      typeof av === "string" && typeof bv === "string"
        ? av.localeCompare(bv)
        : (av as number) - (bv as number);
    return asc ? cmp : -cmp;
  });
}

export interface DirectionGroup {
  direction: SignalDirection;
  results: UniverseRunResult[];
}

export interface GroupedForHeatmap {
  /** One entry per direction that has at least one covered result, in
   * DIRECTION_ORDER, each sorted by confidence descending. */
  groups: DirectionGroup[];
  /** Covered rows with no signal at all (rare: signal null, error null). */
  noSignal: UniverseRunResult[];
  uncovered: UniverseRunResult[];
  failed: UniverseRunResult[];
}

/**
 * Group results for the heatmap (§A2): by direction in fixed strength order,
 * confidence-descending within each group; uncovered/failed collapsed into
 * their own buckets rather than drawn as tiles.
 */
export function groupForHeatmap(results: UniverseRunResult[]): GroupedForHeatmap {
  const groups: DirectionGroup[] = DIRECTION_ORDER.map((direction) => ({
    direction,
    results: [],
  }));
  const groupByDir = new Map(groups.map((g) => [g.direction, g]));
  const noSignal: UniverseRunResult[] = [];
  const uncovered: UniverseRunResult[] = [];
  const failed: UniverseRunResult[] = [];

  for (const r of results) {
    const status = resultStatus(r);
    if (status === "uncovered") {
      uncovered.push(r);
      continue;
    }
    if (status === "failed") {
      failed.push(r);
      continue;
    }
    if (!r.signal) {
      noSignal.push(r);
      continue;
    }
    groupByDir.get(r.signal)?.results.push(r);
  }

  for (const g of groups) {
    g.results.sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
  }

  return {
    groups: groups.filter((g) => g.results.length > 0),
    noSignal,
    uncovered,
    failed,
  };
}

/** Heatmap tile density, keyed off covered-row count per §A2.3. */
export type HeatmapDensity = "labelled" | "compact" | "dense";

export function heatmapDensity(coveredCount: number): HeatmapDensity {
  if (coveredCount <= 60) return "labelled";
  if (coveredCount <= 400) return "compact";
  return "dense";
}

/** Min/max `barTs` across covered rows, for the summary strip's date-context
 * line. Returns null when no row has a bar timestamp. */
export function barTsRange(
  results: UniverseRunResult[],
): { min: number; max: number } | null {
  let min = Infinity;
  let max = -Infinity;
  for (const r of results) {
    if (r.barTs == null) continue;
    if (r.barTs < min) min = r.barTs;
    if (r.barTs > max) max = r.barTs;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  return { min, max };
}

export interface SummarySegment {
  key: "bullish" | "neutral" | "bearish" | "uncovered" | "failed";
  label: string;
  count: number;
  color: string;
}

/** The 5 clickable segments for UniverseSummaryStrip, in display order. */
export function summarySegments(summary: UniverseRunSummary): SummarySegment[] {
  return [
    { key: "bullish", label: "bull", count: summary.bullish, color: "#00C853" },
    { key: "neutral", label: "neutral", count: summary.neutral, color: "#FFD740" },
    { key: "bearish", label: "bear", count: summary.bearish, color: "#D50000" },
    {
      key: "uncovered",
      label: "uncovered",
      count: summary.uncovered,
      color: "#2a2a3e",
    },
    { key: "failed", label: "failed", count: summary.failed, color: "#666" },
  ];
}
