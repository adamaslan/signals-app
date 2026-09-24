import { describe, it, expect } from "vitest";
import {
  aiDegradedShare,
  formatEt,
  newestBarTs,
  pickFeatured,
  sortForHeatmap,
} from "./landing";
import type { LandingSignal } from "./api";

function sig(p: Partial<LandingSignal> & { ticker: string }): LandingSignal {
  return {
    direction: "buy",
    confidence: 0.5,
    confluenceScore: null,
    dataQuality: null,
    aiDegraded: false,
    barTs: null,
    createdAt: null,
    ...p,
  };
}

describe("formatEt", () => {
  it("formats in US Eastern with an ET suffix", () => {
    expect(formatEt("2026-09-19T20:00:00Z")).toBe("Sep 19, 16:00 ET");
  });
  it("returns null for missing or invalid input", () => {
    expect(formatEt(null)).toBeNull();
    expect(formatEt("nope")).toBeNull();
  });
});

describe("aiDegradedShare", () => {
  it("is 0 for an empty list and the fraction otherwise", () => {
    expect(aiDegradedShare([])).toBe(0);
    const rows = [sig({ ticker: "A", aiDegraded: true }), sig({ ticker: "B" })];
    expect(aiDegradedShare(rows)).toBe(0.5);
  });
});

describe("newestBarTs", () => {
  it("picks the latest bar and ignores nulls", () => {
    const rows = [
      sig({ ticker: "A", barTs: "2026-09-18T20:00:00Z" }),
      sig({ ticker: "B", barTs: "2026-09-19T20:00:00Z" }),
      sig({ ticker: "C" }),
    ];
    expect(newestBarTs(rows)).toBe("2026-09-19T20:00:00Z");
    expect(newestBarTs([])).toBeNull();
  });
});

describe("pickFeatured", () => {
  it("returns the top-confidence non-hold signal", () => {
    const rows = [
      sig({ ticker: "H", direction: "hold", confidence: 0.99 }),
      sig({ ticker: "A", confidence: 0.6 }),
      sig({ ticker: "B", direction: "sell", confidence: 0.8 }),
    ];
    expect(pickFeatured(rows)?.ticker).toBe("B");
    expect(pickFeatured([sig({ ticker: "H", direction: "hold" })])).toBeNull();
  });

  it("breaks a confidence tie by |confluence score|", () => {
    const rows = [
      sig({ ticker: "A", confidence: 0.55, confluenceScore: 0.4 }),
      sig({ ticker: "B", direction: "sell", confidence: 0.55, confluenceScore: -0.9 }),
    ];
    expect(pickFeatured(rows)?.ticker).toBe("B");
  });
});

describe("sortForHeatmap", () => {
  it("groups bullish before bearish and does not mutate the input", () => {
    const rows = [
      sig({ ticker: "S", direction: "sell" }),
      sig({ ticker: "B", direction: "strong_buy" }),
      sig({ ticker: "H", direction: "hold" }),
    ];
    expect(sortForHeatmap(rows).map((r) => r.ticker)).toEqual(["B", "H", "S"]);
    expect(rows[0].ticker).toBe("S");
  });
});
