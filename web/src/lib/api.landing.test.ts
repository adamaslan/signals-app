import { describe, it, expect, vi } from "vitest";

vi.mock("./supabase", () => ({ supabase: null, supabaseConfigured: false }));

import {
  buildFunnel,
  fetchLandingSignals,
  fetchPipelineFunnel,
  fetchTopSignals,
  pickTopSignals,
  type LandingSignal,
} from "./api";

function sig(
  ticker: string,
  direction: LandingSignal["direction"],
  confidence: number | null,
): LandingSignal {
  return {
    ticker,
    direction,
    confidence,
    confluenceScore: null,
    dataQuality: null,
    aiDegraded: false,
    barTs: null,
    createdAt: null,
  };
}

describe("pickTopSignals", () => {
  const rows = [
    sig("AAA", "buy", 0.6),
    sig("BBB", "strong_buy", 0.9),
    sig("CCC", "hold", 0.99),
    sig("DDD", "sell", 0.7),
    sig("EEE", "strong_sell", 0.8),
    sig("FFF", "buy", null),
    sig("GGG", "buy", 0.6),
  ];

  it("splits by direction and ranks by confidence, excluding hold", () => {
    const { bullish, bearish } = pickTopSignals(rows, 3);
    expect(bullish.map((r) => r.ticker)).toEqual(["BBB", "AAA", "GGG"]);
    expect(bearish.map((r) => r.ticker)).toEqual(["EEE", "DDD"]);
  });

  it("puts null confidence last and honors the cap", () => {
    const { bullish } = pickTopSignals(rows, 10);
    expect(bullish[bullish.length - 1].ticker).toBe("FFF");
    expect(pickTopSignals(rows, 1).bullish).toHaveLength(1);
  });

  it("breaks confidence ties by |confluence score|, not alphabetically", () => {
    const tied = [
      { ...sig("AAA", "buy", 0.55), confluenceScore: 0.4 },
      { ...sig("ZZZ", "buy", 0.55), confluenceScore: 1.0 },
      { ...sig("MMM", "sell", 0.55), confluenceScore: -0.38 },
      { ...sig("NNN", "sell", 0.55), confluenceScore: -1.0 },
    ];
    const { bullish, bearish } = pickTopSignals(tied, 2);
    expect(bullish.map((r) => r.ticker)).toEqual(["ZZZ", "AAA"]);
    expect(bearish.map((r) => r.ticker)).toEqual(["NNN", "MMM"]);
  });
});

describe("buildFunnel", () => {
  const run = {
    symbols_total: 954,
    symbols_ok: 405,
    symbols_failed: 549,
    finished_at: null,
    started_at: "2026-09-22T21:25:00Z",
  };

  it("derives gated as scanned minus published", () => {
    const f = buildFunnel(run, 288);
    expect(f.published).toBe(288);
    expect(f.gated).toBe(117);
  });

  it("clamps published to scanned so gated never goes negative", () => {
    const f = buildFunnel(run, 900);
    expect(f.published).toBe(405);
    expect(f.gated).toBe(0);
  });
});

describe("with Supabase unset", () => {
  it("returns null instead of throwing", async () => {
    expect(await fetchLandingSignals()).toBeNull();
    expect(await fetchTopSignals("3mo", 5)).toBeNull();
    expect(await fetchPipelineFunnel()).toBeNull();
  });
});
