/**
 * Backtest Lab client — engine-side historical backtests and engine-suggested
 * hypotheses, via the local backend (`POST /backtest/run`,
 * `POST /backtest/suggest`; see src/signals_app/api/routes.py).
 *
 * Distinct from the universe backtest in ./universe.ts, which reads realized
 * `forward_returns` for *published* signals out of Supabase. This one replays
 * every detector over every historical bar, so it answers "does this signal
 * work on these tickers?" even for signals that never cleared the publish
 * gate — which is what testing a hypothesis needs.
 */
import { postBackend } from "./backend";

/** Mirrors `signals_app.config.MAX_MANUAL_BACKTEST_SYMBOLS`. */
export const MAX_BACKTEST_SYMBOLS = 25;
/** Mirrors `signals_app.config.MAX_SUGGEST_BACKTEST_SYMBOLS`. */
export const MAX_SUGGEST_SYMBOLS = 100;
/** Daily-bar history windows the engine can replay (warmup is 200 bars). */
export const BACKTEST_PERIODS = ["1y", "2y", "5y", "10y"] as const;
export const BACKTEST_HORIZONS = [5, 10, 20, 60] as const;

export type FocusGroup = "signal" | "category" | "strength";
export type VerdictStatus = "supported" | "contradicted" | "inconclusive" | "no_data";

export interface Focus {
  group: FocusGroup;
  key: string;
}

export interface EngineBucket {
  key: string;
  hits: number;
  total: number;
  bullish: number;
  hitRate: number;
  lower: number;
  upper: number;
  /** Chance hit-rate for this bucket's bullish/bearish mix; null if unknown. */
  baseline: number | null;
}

export interface FocusVerdict {
  group: FocusGroup;
  key: string;
  status: VerdictStatus;
  hits: number;
  total: number;
  hitRate: number | null;
  lower: number | null;
  upper: number | null;
  baseline: number | null;
  message: string;
}

export interface Verdict {
  status: VerdictStatus;
  message: string;
  focuses: FocusVerdict[];
}

export interface EngineBacktest {
  symbolsOk: string[];
  symbolsFailed: { symbol: string; errorType: string; message: string }[];
  period: string;
  horizonDays: number;
  upRate: number | null;
  scoredBars: number;
  bySignal: EngineBucket[];
  byCategory: EngineBucket[];
  byStrength: EngineBucket[];
  verdict: Verdict | null;
}

export interface Hypothesis {
  id: string;
  kind: "cluster" | "single" | "conflict" | "category" | "strength" | string;
  title: string;
  rationale: string;
  symbols: string[];
  period: string;
  horizonDays: number;
  focus: Focus[];
  priority: number;
}

export interface Suggestions {
  hypotheses: Hypothesis[];
  symbolsOk: string[];
  symbolsFailed: { symbol: string; errorType: string; message: string }[];
  liveSignals: Record<string, string[]>;
}

export interface RunBacktestRequest {
  symbols: string[];
  period?: string;
  horizonDays?: number;
  focus?: Focus[];
}

// -- wire shapes (snake_case) ------------------------------------------------
interface BucketRow {
  key: string;
  hits: number;
  total: number;
  bullish: number;
  hit_rate: number;
  lower: number;
  upper: number;
  baseline: number | null;
}
interface FocusVerdictRow {
  group: FocusGroup;
  key: string;
  status: VerdictStatus;
  hits: number;
  total: number;
  hit_rate: number | null;
  lower: number | null;
  upper: number | null;
  baseline: number | null;
  message: string;
}
interface FailureRow {
  symbol: string;
  error_type: string;
  message: string;
}
interface RunRow {
  symbols_ok: string[];
  symbols_failed: FailureRow[];
  period: string;
  horizon_days: number;
  up_rate: number | null;
  scored_bars: number;
  by_signal: BucketRow[];
  by_category: BucketRow[];
  by_strength: BucketRow[];
  verdict: { status: VerdictStatus; message: string; focuses: FocusVerdictRow[] } | null;
}
interface HypothesisRow {
  id: string;
  kind: string;
  title: string;
  rationale: string;
  symbols: string[];
  period: string;
  horizon_days: number;
  focus: Focus[];
  priority: number;
}
interface SuggestRow {
  hypotheses: HypothesisRow[];
  symbols_ok: string[];
  symbols_failed: FailureRow[];
  live_signals: Record<string, string[]>;
}

const toBucket = (b: BucketRow): EngineBucket => ({
  key: b.key,
  hits: b.hits,
  total: b.total,
  bullish: b.bullish,
  hitRate: b.hit_rate,
  lower: b.lower,
  upper: b.upper,
  baseline: b.baseline,
});
const toFailure = (f: FailureRow) => ({
  symbol: f.symbol,
  errorType: f.error_type,
  message: f.message,
});

/** Normalize a free-text ticker list: split, uppercase, dedupe, keep order. */
export function parseTickers(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const t = raw.replace(/^\$/, "").trim().toUpperCase();
    if (t && /^[A-Z0-9.\-^=]{1,15}$/.test(t) && !seen.has(t)) {
      seen.add(t);
      out.push(t);
    }
  }
  return out;
}

/**
 * Run a basket backtest on the engine; pass `focus` to get a verdict.
 *
 * @throws ApiError — 503 when the local backend isn't running.
 */
export async function runEngineBacktest(req: RunBacktestRequest): Promise<EngineBacktest> {
  const row = await postBackend<RunRow>(
    "backtest/run",
    {
      symbols: req.symbols.slice(0, MAX_BACKTEST_SYMBOLS),
      period: req.period ?? "2y",
      horizon_days: req.horizonDays ?? 20,
      focus: req.focus ?? [],
    },
    "Backtest",
  );
  return {
    symbolsOk: row.symbols_ok,
    symbolsFailed: row.symbols_failed.map(toFailure),
    period: row.period,
    horizonDays: row.horizon_days,
    upRate: row.up_rate,
    scoredBars: row.scored_bars,
    bySignal: row.by_signal.map(toBucket),
    byCategory: row.by_category.map(toBucket),
    byStrength: row.by_strength.map(toBucket),
    verdict: row.verdict
      ? {
          status: row.verdict.status,
          message: row.verdict.message,
          focuses: row.verdict.focuses.map((f) => ({
            group: f.group,
            key: f.key,
            status: f.status,
            hits: f.hits,
            total: f.total,
            hitRate: f.hit_rate,
            lower: f.lower,
            upper: f.upper,
            baseline: f.baseline,
            message: f.message,
          })),
        }
      : null,
  };
}

/**
 * Ask the engine which backtests would test the claims this basket's live
 * signals are making right now.
 *
 * @throws ApiError — 503 when the local backend isn't running.
 */
export async function suggestBacktests(
  symbols: string[],
  opts: { period?: string; horizonDays?: number | null; maxSuggestions?: number } = {},
): Promise<Suggestions> {
  const row = await postBackend<SuggestRow>(
    "backtest/suggest",
    {
      symbols: symbols.slice(0, MAX_SUGGEST_SYMBOLS),
      period: opts.period ?? "2y",
      horizon_days: opts.horizonDays ?? null,
      max_suggestions: opts.maxSuggestions ?? 8,
    },
    "Suggest",
  );
  return {
    hypotheses: row.hypotheses.map((h) => ({
      id: h.id,
      kind: h.kind,
      title: h.title,
      rationale: h.rationale,
      symbols: h.symbols,
      period: h.period,
      horizonDays: h.horizon_days,
      focus: h.focus,
      priority: h.priority,
    })),
    symbolsOk: row.symbols_ok,
    symbolsFailed: row.symbols_failed.map(toFailure),
    liveSignals: row.live_signals,
  };
}

/** Deep link into the lab with a basket (and optional spec) prefilled. */
export function labHref(opts: {
  symbols: string[];
  horizonDays?: number;
  period?: string;
  focus?: Focus[];
  suggest?: boolean;
}): string {
  const q = new URLSearchParams();
  q.set("symbols", opts.symbols.join(","));
  if (opts.horizonDays) q.set("h", String(opts.horizonDays));
  if (opts.period) q.set("period", opts.period);
  if (opts.focus?.length) q.set("focus", opts.focus.map((f) => `${f.group}:${f.key}`).join("|"));
  if (opts.suggest) q.set("suggest", "1");
  return `/backtest/?${q.toString()}`;
}

/** Inverse of the `focus` query param written by {@link labHref}. */
export function parseFocusParam(raw: string | null): Focus[] {
  if (!raw) return [];
  return raw
    .split("|")
    .map((part) => {
      const i = part.indexOf(":");
      const group = part.slice(0, i) as FocusGroup;
      const key = part.slice(i + 1);
      return i > 0 && key && ["signal", "category", "strength"].includes(group)
        ? { group, key }
        : null;
    })
    .filter((f): f is Focus => f !== null);
}
