import { describe, it, expect, vi, beforeEach } from "vitest";
import type { UniverseSignalSnapshot } from "./api";

// ── Mock the Supabase-touching layer ─────────────────────────────────────
const fetchUniverseSignals = vi.fn<
  (tickers: string[], period: string) => Promise<Map<string, UniverseSignalSnapshot>>
>();
const fetchCoverage = vi.fn<
  (tickers: string[]) => Promise<{
    covered: string[];
    inactive: string[];
    uncovered: string[];
  }>
>();
const fetchUniverseHitRates = vi.fn<
  (tickers: string[], horizon: number, bucket: string) => Promise<
    { bucket_key: string; hits: number; total: number; hit_rate: number }[]
  >
>();
const fetchUniverseBacktestMeta = vi.fn<
  (tickers: string[], horizon: number) => Promise<{
    tickersScored: number;
    hitsTotal: number;
    signalsTotal: number;
    baselineUpRate: number | null;
  }>
>();

vi.mock("./api", () => ({
  fetchUniverseSignals: (...a: unknown[]) => fetchUniverseSignals(...(a as [string[], string])),
  fetchCoverage: (...a: unknown[]) => fetchCoverage(...(a as [string[]])),
  fetchUniverseHitRates: (...a: unknown[]) =>
    fetchUniverseHitRates(...(a as [string[], number, string])),
  fetchUniverseBacktestMeta: (...a: unknown[]) =>
    fetchUniverseBacktestMeta(...(a as [string[], number])),
}));

import {
  createUniverse,
  getUniverse,
  listUniverses,
  renameUniverse,
  deleteUniverse,
  addTicker,
  removeTicker,
  setTickers,
  importTickersFromText,
  parseTickerText,
  cleanTickerList,
  exportUniverse,
  importUniverse,
  exportUniverseCsv,
  refreshCoverage,
  runUniverse,
  listUniverseRuns,
  diffRuns,
  wilsonLowerBound,
  getCachedUniverseBacktest,
  backtestUniverse,
  universeFromWatchlist,
  watchlistFromUniverse,
} from "./universe";
import { db } from "./db";

function snap(over: Partial<UniverseSignalSnapshot> = {}): UniverseSignalSnapshot {
  return {
    ticker: "X",
    direction: "buy",
    confidence: 0.7,
    confluenceScore: 0.5,
    dataQuality: 0.9,
    alignmentScore: 0.8,
    divergencePattern: "aligned_bullish",
    aiDegraded: false,
    barTs: Date.now(),
    codeVersion: "v1",
    ...over,
  };
}

beforeEach(() => {
  fetchUniverseSignals.mockReset();
  fetchCoverage.mockReset();
  fetchUniverseHitRates.mockReset();
  fetchUniverseBacktestMeta.mockReset();
});

describe("ticker parsing", () => {
  it("parses comma/space/newline/tab and $ prefixes", () => {
    expect(parseTickerText("AAPL,MSFT").valid).toEqual(["AAPL", "MSFT"]);
    expect(parseTickerText("$aapl msft").valid).toEqual(["AAPL", "MSFT"]);
    expect(parseTickerText("AAPL\nMSFT\nGOOG").valid).toEqual([
      "AAPL",
      "MSFT",
      "GOOG",
    ]);
    expect(parseTickerText("AAPL\tMSFT").valid).toEqual(["AAPL", "MSFT"]);
  });

  it("takes the first token of a line like 'AAPL 100 shares'", () => {
    expect(parseTickerText("AAPL 100 shares").valid).toEqual(["AAPL"]);
  });

  it("parses a pasted CSV with a header row (header lands in invalid or valid harmlessly)", () => {
    const { valid } = parseTickerText("ticker,shares\nAAPL,10\nMSFT,20");
    expect(valid).toContain("AAPL");
    expect(valid).toContain("MSFT");
  });

  it("rejects garbage like '———'", () => {
    const { valid, invalid } = parseTickerText("AAPL,———,MSFT");
    expect(valid).toEqual(["AAPL", "MSFT"]);
    expect(invalid).toContain("———");
  });

  it("cleanTickerList uppercases and de-dupes stably", () => {
    expect(cleanTickerList(["aapl", "AAPL", "msft"])).toEqual(["AAPL", "MSFT"]);
  });
});

describe("CRUD", () => {
  it("creates, reads, lists (updatedAt desc)", async () => {
    const a = await createUniverse("Momentum", { tickers: ["AAPL"] });
    await new Promise((r) => setTimeout(r, 2));
    const b = await createUniverse("Value", { tickers: ["BRK.B"] });
    expect(a.revision).toBe(1);
    expect((await getUniverse(a.id!))?.name).toBe("Momentum");
    const list = await listUniverses();
    expect(list.map((u) => u.name)).toEqual(["Value", "Momentum"]);
    expect(b.tickers).toEqual(["BRK.B"]);
  });

  it("rejects a duplicate name (case-insensitive)", async () => {
    await createUniverse("Momentum");
    await expect(createUniverse("momentum")).rejects.toThrow(/already exists/);
    const u = await createUniverse("Other");
    await expect(renameUniverse(u.id!, "MOMENTUM")).rejects.toThrow(/already exists/);
  });

  it("deleteUniverse cascades runs and backtests", async () => {
    const u = await createUniverse("Temp", { tickers: ["AAPL"] });
    fetchUniverseSignals.mockResolvedValue(new Map([["AAPL", snap({ ticker: "AAPL" })]]));
    await runUniverse(u.id!);
    await db!.universeBacktests.add({
      universeId: u.id!,
      universeRevision: 1,
      horizonDays: 20,
      ranAt: Date.now(),
      byStrength: [],
      byCategory: [],
      byTicker: [],
      tickersScored: 1,
      tickersRequested: 1,
      hitsTotal: 0,
      signalsTotal: 0,
      baselineUpRate: null,
    });
    await deleteUniverse(u.id!);
    expect(await getUniverse(u.id!)).toBeNull();
    expect(await db!.universeRuns.where("universeId").equals(u.id!).count()).toBe(0);
    expect(
      await db!.universeBacktests.where("universeId").equals(u.id!).count(),
    ).toBe(0);
  });
});

describe("membership", () => {
  it("bumps revision and invalidates coverage on every mutation", async () => {
    const u = await createUniverse("M", { tickers: ["AAPL"] });
    fetchCoverage.mockResolvedValue({
      covered: ["AAPL"],
      inactive: [],
      uncovered: [],
    });
    await refreshCoverage(u.id!);
    expect((await getUniverse(u.id!))?.coverage).not.toBeNull();

    await addTicker(u.id!, "msft");
    let cur = await getUniverse(u.id!);
    expect(cur?.revision).toBe(2);
    expect(cur?.tickers).toEqual(["AAPL", "MSFT"]);
    expect(cur?.coverage).toBeNull();

    await removeTicker(u.id!, "AAPL");
    cur = await getUniverse(u.id!);
    expect(cur?.revision).toBe(3);
    expect(cur?.tickers).toEqual(["MSFT"]);

    await setTickers(u.id!, ["GOOG", "GOOG", "nvda"]);
    cur = await getUniverse(u.id!);
    expect(cur?.revision).toBe(4);
    expect(cur?.tickers).toEqual(["GOOG", "NVDA"]);
  });

  it("does not bump revision when a mutation is a no-op", async () => {
    const u = await createUniverse("M", { tickers: ["AAPL"] });
    await addTicker(u.id!, "AAPL");
    expect((await getUniverse(u.id!))?.revision).toBe(1);
  });

  it("importTickersFromText returns added / skipped / invalid buckets", async () => {
    const u = await createUniverse("M", { tickers: ["AAPL"] });
    const res = await importTickersFromText(u.id!, "AAPL, MSFT, GOOG, ———, $nvda");
    expect(res.added.sort()).toEqual(["GOOG", "MSFT", "NVDA"]);
    expect(res.skipped).toEqual(["AAPL"]);
    expect(res.invalid).toContain("———");
    expect((await getUniverse(u.id!))?.tickers.sort()).toEqual([
      "AAPL",
      "GOOG",
      "MSFT",
      "NVDA",
    ]);
  });
});

describe("import / export", () => {
  it("round-trips a universe with runs", async () => {
    const u = await createUniverse("Export me", {
      tickers: ["AAPL", "MSFT"],
      note: "thesis",
    });
    fetchUniverseSignals.mockResolvedValue(
      new Map([
        ["AAPL", snap({ ticker: "AAPL", direction: "strong_buy" })],
        ["MSFT", snap({ ticker: "MSFT", direction: "sell" })],
      ]),
    );
    await runUniverse(u.id!);
    const dto = await exportUniverse(u.id!);
    expect(dto.universe.name).toBe("Export me");
    expect(dto.runs).toHaveLength(1);

    const imported = await importUniverse(dto);
    expect(imported.name).toBe("Export me (imported)");
    expect(imported.tickers).toEqual(["AAPL", "MSFT"]);
    expect(
      await db!.universeRuns.where("universeId").equals(imported.id!).count(),
    ).toBe(1);
  });

  it("exports CSV with the newest run result per ticker", async () => {
    const u = await createUniverse("CSV", { tickers: ["AAPL", "ZZZZ"] });
    fetchUniverseSignals.mockResolvedValue(
      new Map([["AAPL", snap({ ticker: "AAPL", direction: "buy", confidence: 0.6 })]]),
    );
    await runUniverse(u.id!);
    const csv = await exportUniverseCsv(u.id!);
    const lines = csv.split("\n");
    expect(lines[0]).toBe(
      "ticker,signal,confidence,confluence,data_quality,error",
    );
    expect(lines.find((l) => l.startsWith("AAPL,"))).toContain("buy,0.6");
    expect(lines.find((l) => l.startsWith("ZZZZ,"))).toContain("uncovered");
  });
});

describe("coverage", () => {
  it("classifies covered / inactive / uncovered and caches", async () => {
    const u = await createUniverse("C", { tickers: ["AAPL", "RIVN", "MYCO"] });
    fetchCoverage.mockResolvedValue({
      covered: ["AAPL"],
      inactive: ["RIVN"],
      uncovered: ["MYCO"],
    });
    const cov = await refreshCoverage(u.id!);
    expect(cov?.covered).toEqual(["AAPL"]);
    expect(cov?.inactive).toEqual(["RIVN"]);
    expect(cov?.uncovered).toEqual(["MYCO"]);
    expect((await getUniverse(u.id!))?.coverage?.checkedAt).toBe(cov?.checkedAt);
  });
});

describe("runUniverse", () => {
  it("marks tickers with no row as error:'uncovered', not failed", async () => {
    const u = await createUniverse("R", { tickers: ["AAPL", "MSFT", "NOPE"] });
    fetchUniverseSignals.mockResolvedValue(
      new Map([
        ["AAPL", snap({ ticker: "AAPL", direction: "buy" })],
        ["MSFT", snap({ ticker: "MSFT", direction: "strong_sell" })],
      ]),
    );
    const run = await runUniverse(u.id!);
    const nope = run.results.find((r) => r.ticker === "NOPE")!;
    expect(nope.error).toBe("uncovered");
    expect(nope.signal).toBeNull();
    expect(run.summary!.uncovered).toBe(1);
    expect(run.summary!.failed).toBe(0);
    expect(run.summary!.bullish).toBe(1);
    expect(run.summary!.bearish).toBe(1);
    expect(run.status).toBe("partial");
    expect(run.universeRevision).toBe(1);
  });

  it("marks the whole run failed on a fetch error, keeping per-ticker error text", async () => {
    const u = await createUniverse("R", { tickers: ["AAPL"] });
    fetchUniverseSignals.mockRejectedValue(new Error("network down"));
    const run = await runUniverse(u.id!);
    expect(run.status).toBe("failed");
    expect(run.results[0].error).toBe("network down");
    expect(run.summary!.failed).toBe(1);
  });

  it("status 'complete' when every ticker resolves", async () => {
    const u = await createUniverse("R", { tickers: ["AAPL", "MSFT"] });
    fetchUniverseSignals.mockResolvedValue(
      new Map([
        ["AAPL", snap({ ticker: "AAPL" })],
        ["MSFT", snap({ ticker: "MSFT" })],
      ]),
    );
    const run = await runUniverse(u.id!);
    expect(run.status).toBe("complete");
    expect((await listUniverseRuns(u.id!))[0].id).toBe(run.id);
  });

  it("averages confidence / data quality / alignment over covered tickers only", async () => {
    const u = await createUniverse("R", { tickers: ["A", "B", "C"] });
    fetchUniverseSignals.mockResolvedValue(
      new Map([
        ["A", snap({ ticker: "A", confidence: 0.4, dataQuality: 0.8, alignmentScore: 0.6 })],
        ["B", snap({ ticker: "B", confidence: 0.6, dataQuality: 1.0, alignmentScore: 1.0 })],
      ]),
    );
    const run = await runUniverse(u.id!);
    expect(run.summary!.avgConfidence).toBeCloseTo(0.5);
    expect(run.summary!.avgDataQuality).toBeCloseTo(0.9);
    expect(run.summary!.avgAlignment).toBeCloseTo(0.8);
  });
});

describe("diffRuns", () => {
  it("classifies upgraded / downgraded / unchanged / newly-covered / went-stale", async () => {
    const u = await createUniverse("D", { tickers: ["UP", "DOWN", "SAME", "COV", "STALE"] });
    fetchUniverseSignals.mockResolvedValueOnce(
      new Map([
        ["UP", snap({ ticker: "UP", direction: "hold" })],
        ["DOWN", snap({ ticker: "DOWN", direction: "buy" })],
        ["SAME", snap({ ticker: "SAME", direction: "buy" })],
        ["STALE", snap({ ticker: "STALE", direction: "buy" })],
      ]),
    );
    const r1 = await runUniverse(u.id!);
    fetchUniverseSignals.mockResolvedValueOnce(
      new Map([
        ["UP", snap({ ticker: "UP", direction: "strong_buy" })],
        ["DOWN", snap({ ticker: "DOWN", direction: "sell" })],
        ["SAME", snap({ ticker: "SAME", direction: "buy" })],
        ["COV", snap({ ticker: "COV", direction: "buy" })],
      ]),
    );
    const r2 = await runUniverse(u.id!);

    const diff = await diffRuns(r1.id!, r2.id!);
    const byT = Object.fromEntries(diff.entries.map((e) => [e.ticker, e.class]));
    expect(byT.UP).toBe("upgraded");
    expect(byT.DOWN).toBe("downgraded");
    expect(byT.SAME).toBe("unchanged");
    expect(byT.COV).toBe("newly-covered");
    expect(byT.STALE).toBe("went-stale");
    expect(diff.revisionMismatch).toBe(false);
  });

  it("flags revisionMismatch when membership changed between runs", async () => {
    const u = await createUniverse("D", { tickers: ["AAPL"] });
    fetchUniverseSignals.mockResolvedValue(
      new Map([["AAPL", snap({ ticker: "AAPL" })]]),
    );
    const r1 = await runUniverse(u.id!);
    await addTicker(u.id!, "MSFT");
    fetchUniverseSignals.mockResolvedValue(
      new Map([
        ["AAPL", snap({ ticker: "AAPL" })],
        ["MSFT", snap({ ticker: "MSFT" })],
      ]),
    );
    const r2 = await runUniverse(u.id!);
    const diff = await diffRuns(r1.id!, r2.id!);
    expect(diff.revisionMismatch).toBe(true);
    expect(diff.prevRevision).toBe(1);
    expect(diff.nextRevision).toBe(2);
    expect(diff.entries.find((e) => e.ticker === "MSFT")?.class).toBe("new");
  });
});

describe("wilson bounds + backtest cache", () => {
  it("n=3 hits=3 → hit rate 1.0 but lower bound ≈ 0.44", () => {
    expect(wilsonLowerBound(3, 3)).toBeCloseTo(0.4385, 3);
    expect(wilsonLowerBound(0, 0)).toBe(0);
    expect(wilsonLowerBound(50, 100)).toBeGreaterThan(0.4);
    expect(wilsonLowerBound(50, 100)).toBeLessThan(0.5);
  });

  it("backtestUniverse returns a cached row when present without calling the RPC", async () => {
    const u = await createUniverse("B", { tickers: ["AAPL"] });
    await db!.universeBacktests.add({
      universeId: u.id!,
      universeRevision: 1,
      horizonDays: 20,
      ranAt: Date.now(),
      byStrength: [],
      byCategory: [],
      byTicker: [],
      tickersScored: 1,
      tickersRequested: 1,
      hitsTotal: 3,
      signalsTotal: 4,
      baselineUpRate: 0.5,
    });
    const got = await getCachedUniverseBacktest(u.id!, 20);
    expect(got?.hitsTotal).toBe(3);
    expect((await backtestUniverse(u.id!, 20)).hitsTotal).toBe(3);
    expect(fetchUniverseHitRates).not.toHaveBeenCalled();
  });

  it("backtestUniverse computes via the RPC on a cache miss and caches the result", async () => {
    const u = await createUniverse("B", { tickers: ["AAPL", "MSFT"] });
    fetchUniverseHitRates.mockImplementation(async (_t, _h, bucket) => {
      if (bucket === "strength")
        return [{ bucket_key: "BULLISH", hits: 30, total: 50, hit_rate: 0.6 }];
      if (bucket === "category")
        return [{ bucket_key: "momentum", hits: 20, total: 40, hit_rate: 0.5 }];
      return [{ bucket_key: "AAPL", hits: 18, total: 30, hit_rate: 0.6 }];
    });
    fetchUniverseBacktestMeta.mockResolvedValue({
      tickersScored: 1,
      hitsTotal: 30,
      signalsTotal: 50,
      baselineUpRate: 0.52,
    });

    const bt = await backtestUniverse(u.id!, 20);
    expect(bt.byStrength[0].key).toBe("BULLISH");
    expect(bt.byStrength[0].hitRate).toBeCloseTo(0.6);
    expect(bt.byStrength[0].hitRateLower).toBeLessThan(0.6);
    expect(bt.byCategory[0].key).toBe("momentum");
    expect(bt.byTicker[0].key).toBe("AAPL");
    expect(bt.tickersScored).toBe(1);
    expect(bt.tickersRequested).toBe(2);
    expect(bt.baselineUpRate).toBeCloseTo(0.52);
    expect(fetchUniverseHitRates).toHaveBeenCalledTimes(3);

    // Second call hits the cache — no further RPC calls.
    fetchUniverseHitRates.mockClear();
    const again = await backtestUniverse(u.id!, 20);
    expect(again.id).toBe(bt.id);
    expect(fetchUniverseHitRates).not.toHaveBeenCalled();

    // force:true recomputes.
    await backtestUniverse(u.id!, 20, { force: true });
    expect(fetchUniverseHitRates).toHaveBeenCalledTimes(3);
  });

  it("cache miss after a membership change (revision is part of the key)", async () => {
    const u = await createUniverse("B", { tickers: ["AAPL"] });
    await db!.universeBacktests.add({
      universeId: u.id!,
      universeRevision: 1,
      horizonDays: 20,
      ranAt: Date.now(),
      byStrength: [],
      byCategory: [],
      byTicker: [],
      tickersScored: 1,
      tickersRequested: 1,
      hitsTotal: 3,
      signalsTotal: 4,
      baselineUpRate: 0.5,
    });
    await addTicker(u.id!, "MSFT");
    expect(await getCachedUniverseBacktest(u.id!, 20)).toBeNull();
  });
});

describe("watchlist bridges", () => {
  it("universeFromWatchlist snapshots the flat list", async () => {
    await db!.watchlist.bulkPut([
      { ticker: "AAPL", note: "", targetPrice: null, addedAt: 1, lastSignal: null, lastCheckedAt: null },
      { ticker: "MSFT", note: "", targetPrice: null, addedAt: 2, lastSignal: null, lastCheckedAt: null },
    ]);
    const u = await universeFromWatchlist("From WL");
    expect(u.tickers.sort()).toEqual(["AAPL", "MSFT"]);
  });

  it("watchlistFromUniverse adds only missing tickers", async () => {
    await db!.watchlist.put({
      ticker: "AAPL",
      note: "",
      targetPrice: null,
      addedAt: 1,
      lastSignal: null,
      lastCheckedAt: null,
    });
    const u = await createUniverse("U", { tickers: ["AAPL", "MSFT", "GOOG"] });
    const added = await watchlistFromUniverse(u.id!);
    expect(added).toBe(2);
    expect(await db!.watchlist.count()).toBe(3);
  });
});
