import { describe, it, expect } from "vitest";
import type { UniverseRunResult, UniverseRunSummary } from "./db";
import {
  defaultFilter,
  filterAndSortResults,
  groupForHeatmap,
  heatmapDensity,
  barTsRange,
  summarySegments,
  resultStatus,
  DIRECTION_ORDER,
} from "./universeView";

function result(over: Partial<UniverseRunResult>): UniverseRunResult {
  return {
    ticker: "AAA",
    signal: null,
    confidence: null,
    confluenceScore: null,
    dataQuality: null,
    alignmentScore: null,
    divergencePattern: null,
    aiDegraded: false,
    barTs: null,
    codeVersion: null,
    error: null,
    ...over,
  };
}

describe("resultStatus", () => {
  it("classifies uncovered / failed / covered", () => {
    expect(resultStatus(result({ error: "uncovered" }))).toBe("uncovered");
    expect(resultStatus(result({ error: "boom" }))).toBe("failed");
    expect(resultStatus(result({ error: null }))).toBe("covered");
  });
});

describe("defaultFilter", () => {
  it("hides uncovered by default only past 100 results", () => {
    expect(defaultFilter(50).hideUncovered).toBe(false);
    expect(defaultFilter(101).hideUncovered).toBe(true);
  });
});

describe("filterAndSortResults", () => {
  const rows = [
    result({ ticker: "AAA", signal: "strong_buy", confidence: 0.9 }),
    result({ ticker: "BBB", signal: "sell", confidence: 0.4 }),
    result({ ticker: "CCC", error: "uncovered" }),
    result({ ticker: "DDD", error: "fetch failed" }),
  ];

  it("hides uncovered rows when hideUncovered is set", () => {
    const f = { ...defaultFilter(0), hideUncovered: true };
    const out = filterAndSortResults(rows, f);
    expect(out.map((r) => r.ticker)).toEqual(["AAA", "BBB", "DDD"]);
  });

  it("keeps failed rows even when hideUncovered is set (distinct from uncovered)", () => {
    const f = { ...defaultFilter(0), hideUncovered: true };
    const out = filterAndSortResults(rows, f);
    expect(out.some((r) => r.ticker === "DDD")).toBe(true);
  });

  it("filters by direction", () => {
    const f = { ...defaultFilter(0), directions: new Set(["sell" as const]) };
    const out = filterAndSortResults(rows, f);
    expect(out.map((r) => r.ticker)).toEqual(["BBB"]);
  });

  it("filters by minConfidence", () => {
    const f = { ...defaultFilter(0), minConfidence: 0.5 };
    const out = filterAndSortResults(rows, f);
    expect(out.map((r) => r.ticker)).toEqual(["AAA"]);
  });

  it("filters by ticker search prefix, case-insensitive", () => {
    const f = { ...defaultFilter(0), tickerSearch: "bb" };
    const out = filterAndSortResults(rows, f);
    expect(out.map((r) => r.ticker)).toEqual(["BBB"]);
  });

  it("sorts by the given key, descending by default", () => {
    const out = filterAndSortResults(rows, defaultFilter(0), {
      sortKey: "confidence",
    });
    expect(out[0].ticker).toBe("AAA");
  });

  it("sorts ascending when sortAsc is true", () => {
    const out = filterAndSortResults(rows, defaultFilter(0), {
      sortKey: "ticker",
      sortAsc: true,
    });
    expect(out.map((r) => r.ticker)).toEqual(["AAA", "BBB", "CCC", "DDD"]);
  });
});

describe("groupForHeatmap", () => {
  it("groups covered rows by direction in strength order", () => {
    const rows = [
      result({ ticker: "A", signal: "sell", confidence: 0.5 }),
      result({ ticker: "B", signal: "strong_buy", confidence: 0.9 }),
      result({ ticker: "C", signal: "strong_buy", confidence: 0.6 }),
    ];
    const g = groupForHeatmap(rows);
    expect(g.groups.map((grp) => grp.direction)).toEqual(["strong_buy", "sell"]);
    // Sorted by confidence descending within the group.
    expect(g.groups[0].results.map((r) => r.ticker)).toEqual(["B", "C"]);
  });

  it("collapses uncovered and failed into their own buckets, not groups", () => {
    const rows = [
      result({ ticker: "A", error: "uncovered" }),
      result({ ticker: "B", error: "boom" }),
    ];
    const g = groupForHeatmap(rows);
    expect(g.groups).toEqual([]);
    expect(g.uncovered.map((r) => r.ticker)).toEqual(["A"]);
    expect(g.failed.map((r) => r.ticker)).toEqual(["B"]);
  });

  it("respects DIRECTION_ORDER (strong_buy -> strong_sell)", () => {
    expect(DIRECTION_ORDER).toEqual([
      "strong_buy",
      "buy",
      "hold",
      "sell",
      "strong_sell",
    ]);
  });
});

describe("heatmapDensity", () => {
  it("picks labelled / compact / dense by covered count", () => {
    expect(heatmapDensity(10)).toBe("labelled");
    expect(heatmapDensity(60)).toBe("labelled");
    expect(heatmapDensity(61)).toBe("compact");
    expect(heatmapDensity(400)).toBe("compact");
    expect(heatmapDensity(401)).toBe("dense");
    expect(heatmapDensity(954)).toBe("dense");
  });
});

describe("barTsRange", () => {
  it("returns null when no row has a barTs", () => {
    expect(barTsRange([result({})])).toBeNull();
  });

  it("returns min/max across covered rows", () => {
    const rows = [
      result({ barTs: 300 }),
      result({ barTs: 100 }),
      result({ barTs: 200 }),
    ];
    expect(barTsRange(rows)).toEqual({ min: 100, max: 300 });
  });
});

describe("summarySegments", () => {
  it("produces 5 segments in display order", () => {
    const summary: UniverseRunSummary = {
      counted: 10,
      bullish: 3,
      bearish: 2,
      neutral: 5,
      failed: 1,
      uncovered: 4,
      avgConfidence: 0.5,
      avgDataQuality: 0.8,
      avgAlignment: 0.6,
    };
    const segs = summarySegments(summary);
    expect(segs.map((s) => s.key)).toEqual([
      "bullish",
      "neutral",
      "bearish",
      "uncovered",
      "failed",
    ]);
    expect(segs.map((s) => s.count)).toEqual([3, 5, 2, 4, 1]);
  });
});
