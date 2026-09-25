import { describe, it, expect, vi, afterEach } from "vitest";
import {
  labHref,
  parseFocusParam,
  parseTickers,
  runEngineBacktest,
  suggestBacktests,
  MAX_BACKTEST_SYMBOLS,
} from "./backtestLab";
import { backendUrl } from "./backend";
import { ApiError } from "./api";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () =>
    new Response(body == null ? "" : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("backendUrl", () => {
  it("ends in a slash so trailingSlash never 308s the POST", () => {
    expect(backendUrl("backtest/run")).toMatch(/\/api\/backtest\/run\/$/);
    expect(backendUrl("/scan/")).toMatch(/\/api\/scan\/$/);
  });
});

describe("parseTickers", () => {
  it("splits, uppercases, strips $ and dedupes in order", () => {
    expect(parseTickers("aapl, $msft\nNVDA aapl;brk.b")).toEqual(["AAPL", "MSFT", "NVDA", "BRK.B"]);
  });
  it("drops junk tokens", () => {
    expect(parseTickers("  , ,, <script>")).toEqual([]);
  });
});

describe("labHref / parseFocusParam", () => {
  it("round-trips a hypothesis spec, including keys with spaces and colons", () => {
    const focus = [
      { group: "signal" as const, key: "STOCH BULL CROSS (OVERSOLD)" },
      { group: "strength" as const, key: "STRONG BULLISH" },
    ];
    const href = labHref({ symbols: ["AAPL", "MSFT"], horizonDays: 10, period: "5y", focus });
    const q = new URLSearchParams(href.split("?")[1]);
    expect(q.get("symbols")).toBe("AAPL,MSFT");
    expect(q.get("h")).toBe("10");
    expect(parseFocusParam(q.get("focus"))).toEqual(focus);
  });
  it("ignores malformed focus parts", () => {
    expect(parseFocusParam("bogus:X|signal:|noColon|category:RSI")).toEqual([
      { group: "category", key: "RSI" },
    ]);
  });
});

describe("runEngineBacktest", () => {
  it("posts the capped spec and maps snake_case to camelCase", async () => {
    const fetchFn = mockFetch(200, {
      symbols_ok: ["AAPL"],
      symbols_failed: [{ symbol: "BAD", error_type: "SymbolNotFound", message: "x" }],
      period: "2y",
      horizon_days: 20,
      up_rate: 0.55,
      scored_bars: 300,
      by_signal: [
        { key: "GOLDEN CROSS", hits: 40, total: 50, bullish: 50, hit_rate: 0.8, lower: 0.67, upper: 0.89, baseline: 0.55 },
      ],
      by_category: [],
      by_strength: [],
      verdict: {
        status: "supported",
        message: "ok",
        focuses: [
          { group: "signal", key: "GOLDEN CROSS", status: "supported", hits: 40, total: 50, hit_rate: 0.8, lower: 0.67, upper: 0.89, baseline: 0.55, message: "ok" },
        ],
      },
    });
    const symbols = Array.from({ length: MAX_BACKTEST_SYMBOLS + 5 }, (_, i) => `T${i}`);
    const res = await runEngineBacktest({ symbols, focus: [{ group: "signal", key: "GOLDEN CROSS" }] });

    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    const sent = JSON.parse(init.body as string);
    expect(sent.symbols).toHaveLength(MAX_BACKTEST_SYMBOLS);
    expect(sent.horizon_days).toBe(20);
    expect(res.upRate).toBe(0.55);
    expect(res.bySignal[0]).toMatchObject({ key: "GOLDEN CROSS", hitRate: 0.8, baseline: 0.55 });
    expect(res.symbolsFailed[0].errorType).toBe("SymbolNotFound");
    expect(res.verdict?.focuses[0].hitRate).toBe(0.8);
  });

  it("surfaces FastAPI detail on a 400", async () => {
    mockFetch(400, { detail: "Invalid period '7w'" });
    await expect(runEngineBacktest({ symbols: ["AAPL"], period: "7w" })).rejects.toThrow(/Invalid period/);
  });

  it("reports an unreachable backend as 503 when the rewrite 404s", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>404</html>", { status: 404 })));
    const err = await runEngineBacktest({ symbols: ["AAPL"] }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(503);
  });
});

describe("suggestBacktests", () => {
  it("maps hypotheses", async () => {
    mockFetch(200, {
      hypotheses: [
        {
          id: "abc",
          kind: "cluster",
          title: "t",
          rationale: "r",
          symbols: ["AAPL", "MSFT"],
          period: "2y",
          horizon_days: 20,
          focus: [{ group: "signal", key: "GOLDEN CROSS" }],
          priority: 2.5,
        },
      ],
      symbols_ok: ["AAPL", "MSFT"],
      symbols_failed: [],
      live_signals: { AAPL: ["GOLDEN CROSS"] },
      max_backtest_symbols: 25,
    });
    const s = await suggestBacktests(["AAPL", "MSFT"]);
    expect(s.hypotheses[0]).toMatchObject({ id: "abc", horizonDays: 20, symbols: ["AAPL", "MSFT"] });
    expect(s.liveSignals.AAPL).toEqual(["GOLDEN CROSS"]);
  });
});
