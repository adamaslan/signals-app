import { supabase, supabaseConfigured } from "./supabase";
import type {
  EvidenceItem,
  Signal,
  SignalDirection,
  SignalOutput,
  Timeframe,
  TimeframeMatrix,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Thrown when Supabase has no signal row for this ticker/period yet — the
 * scanner hasn't covered it, distinct from a real fetch failure so the UI
 * can render "not scanned yet" instead of an error state. */
export class SignalNotFoundError extends Error {
  constructor(symbol: string, period: string) {
    super(
      `No signal for ${symbol} (${period}) yet. It may be outside the current ` +
        `scan universe, or the last scan didn't clear the publication gate.`
    );
    this.name = "SignalNotFoundError";
  }
}

const PERIOD_TO_TIMEFRAME: Record<string, Timeframe> = {
  "1d": "1D",
  "5d": "5D",
  "1mo": "1M",
  "3mo": "3M",
  "6mo": "6M",
  "1y": "1Y",
};

interface SignalRow {
  ticker: string;
  period: string;
  /** The bar this signal describes — distinct from `created_at` (§5.1). */
  bar_ts: string | null;
  direction: string | null;
  confidence: number | null;
  confluence_score: number | null;
  bias: string;
  bull_count: number;
  bear_count: number;
  total_signals: number;
  data_quality_score: number | null;
  data_quality_reasons: string[];
  evidence: EvidenceItem[];
  counter_evidence: EvidenceItem[];
  matrix: TimeframeMatrix | null;
  ai_degraded: boolean;
  no_llm: boolean;
  prompt_version: string | null;
  code_version: string;
  created_at: string;
}

const SIGNAL_ROW_COLUMNS =
  "ticker,period,bar_ts,direction,confidence,confluence_score,bias,bull_count," +
  "bear_count,total_signals,data_quality_score,data_quality_reasons,evidence," +
  "counter_evidence,matrix,ai_degraded,no_llm,prompt_version,code_version,created_at";

function rowToSignalOutput(row: SignalRow, period: string): SignalOutput {
  const timeframe = PERIOD_TO_TIMEFRAME[period] ?? "3M" as Timeframe;
  const direction = (row.direction ?? "hold") as SignalDirection;

  const signal: Signal = {
    direction,
    // Supabase confidence can be null (an unsynthesized/gated-but-stored
    // row); the Signal schema requires (0,1) exclusive, so fall back to a
    // neutral midpoint rather than crash the UI on a null.
    confidence: row.confidence ?? 0.5,
    timeframe,
    evidence: { items: [...row.evidence, ...row.counter_evidence] },
    ai_degraded: row.ai_degraded,
    prompt_version: row.prompt_version ?? "unknown",
  };

  return {
    ticker: row.ticker,
    signal,
    matrix: row.matrix ?? null,
    feature_unavailable: row.no_llm ? ["llm_synthesis"] : [],
    schema_version: "1.0",
    code_version: row.code_version,
    data_quality_score: row.data_quality_score,
    data_quality_reasons: row.data_quality_reasons ?? [],
    bar_ts: row.bar_ts,
    confluence_score: row.confluence_score,
    created_at: row.created_at,
  };
}

/** Columns pulled for universe list/run rendering — omits the heavy
 * `evidence` / `counter_evidence` JSONB, keeping the batched read small. */
const UNIVERSE_SIGNAL_COLUMNS =
  "ticker,period,bar_ts,direction,confidence,confluence_score,data_quality_score," +
  "matrix,ai_degraded,code_version,created_at";

/** One newest-per-ticker signal snapshot for a universe refresh. */
export interface UniverseSignalSnapshot {
  ticker: string;
  direction: SignalDirection | null;
  confidence: number | null;
  confluenceScore: number | null;
  dataQuality: number | null;
  alignmentScore: number | null;
  divergencePattern: string | null;
  aiDegraded: boolean;
  barTs: number | null;
  codeVersion: string | null;
}

interface UniverseSignalRow {
  ticker: string;
  period: string;
  bar_ts: string | null;
  direction: string | null;
  confidence: number | null;
  confluence_score: number | null;
  data_quality_score: number | null;
  matrix: TimeframeMatrix | null;
  ai_degraded: boolean;
  code_version: string;
  created_at: string;
}

const IN_CHUNK = 200;

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

function rowToSnapshot(row: UniverseSignalRow): UniverseSignalSnapshot {
  return {
    ticker: row.ticker,
    direction: (row.direction as SignalDirection | null) ?? null,
    confidence: row.confidence,
    confluenceScore: row.confluence_score,
    dataQuality: row.data_quality_score,
    alignmentScore: row.matrix?.alignment_score ?? null,
    divergencePattern: row.matrix?.divergence_pattern ?? null,
    aiDegraded: row.ai_degraded,
    barTs: row.bar_ts ? new Date(row.bar_ts).getTime() : null,
    codeVersion: row.code_version ?? null,
  };
}

/**
 * Fetch the newest published signal for many tickers in one (or a few)
 * round-trips. Because reads go direct-to-Postgres, `.in()` does the
 * batching — a universe refresh is 1–3 queries, never a fan-out of N.
 *
 * Returns a Map keyed by ticker. Tickers absent from the Map are
 * **uncovered** (the scanner has no row), which the caller must render
 * distinctly from a failure — see the spec's §3.4.
 *
 * @param tickers - Uppercased ticker symbols.
 * @param period - Backend period string (e.g. "3mo").
 * @throws ApiError if Supabase isn't configured or a query fails.
 */
export async function fetchUniverseSignals(
  tickers: string[],
  period: string,
): Promise<Map<string, UniverseSignalSnapshot>> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured");
  }
  const newest = new Map<string, UniverseSignalSnapshot>();
  if (tickers.length === 0) return newest;

  for (const batch of chunk(tickers, IN_CHUNK)) {
    // `latest_signals` (migration 20260831000001) is DISTINCT ON (ticker,
    // period) — one indexed row per ticker, no client-side de-dup. Falls
    // back to the raw `signals` table + newest-wins loop if the view is
    // missing (older DB), so a not-yet-migrated environment still works.
    let rows: UniverseSignalRow[] | null = null;
    const viewRes = await supabase
      .from("latest_signals")
      .select(UNIVERSE_SIGNAL_COLUMNS)
      .in("ticker", batch)
      .eq("period", period);
    if (!viewRes.error) {
      rows = (viewRes.data ?? []) as unknown as UniverseSignalRow[];
    } else {
      const rawRes = await supabase
        .from("signals")
        .select(UNIVERSE_SIGNAL_COLUMNS)
        .in("ticker", batch)
        .eq("period", period)
        .order("bar_ts", { ascending: false });
      if (rawRes.error) throw new ApiError(500, rawRes.error.message);
      rows = (rawRes.data ?? []) as unknown as UniverseSignalRow[];
    }

    for (const row of rows) {
      // From the view each ticker appears once; from the raw fallback the
      // first (newest by bar_ts) wins.
      if (!newest.has(row.ticker)) newest.set(row.ticker, rowToSnapshot(row));
    }
  }
  return newest;
}

/* ────────────────────────────────────────────────────────────────────────
 * Universe backtest — the `universe_hit_rates` / `universe_backtest_meta`
 * RPCs (migration 20260831000002). Aggregate-only; the underlying
 * detector_hits / forward_returns tables are never exposed to the browser.
 * ──────────────────────────────────────────────────────────────────────── */

export type BacktestBucketKind = "strength" | "category" | "ticker" | "detector";

export interface RawHitRateBucket {
  bucket_key: string;
  hits: number;
  total: number;
  hit_rate: number;
}

export interface UniverseBacktestMeta {
  tickersScored: number;
  hitsTotal: number;
  signalsTotal: number;
  baselineUpRate: number | null;
}

/** Call `universe_hit_rates` for one bucketing. */
export async function fetchUniverseHitRates(
  tickers: string[],
  horizonDays: number,
  bucket: BacktestBucketKind,
): Promise<RawHitRateBucket[]> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured");
  }
  if (tickers.length > 500) {
    throw new ApiError(400, "Universe backtest supports at most 500 tickers");
  }
  const { data, error } = await supabase.rpc("universe_hit_rates", {
    p_tickers: tickers,
    p_horizon_days: horizonDays,
    p_bucket: bucket,
  });
  if (error) {
    // A missing function (migration not applied) is a distinct, actionable case.
    if (/function .*universe_hit_rates.* does not exist/i.test(error.message)) {
      throw new ApiError(
        501,
        "Universe backtest RPC not deployed (migration 20260831000002)",
      );
    }
    throw new ApiError(500, error.message);
  }
  return (data ?? []) as RawHitRateBucket[];
}

/** Call `universe_backtest_meta` for coverage + baseline numbers. */
export async function fetchUniverseBacktestMeta(
  tickers: string[],
  horizonDays: number,
): Promise<UniverseBacktestMeta> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured");
  }
  const { data, error } = await supabase.rpc("universe_backtest_meta", {
    p_tickers: tickers,
    p_horizon_days: horizonDays,
  });
  if (error) throw new ApiError(500, error.message);
  const row = (Array.isArray(data) ? data[0] : data) as
    | {
        tickers_scored: number;
        hits_total: number;
        signals_total: number;
        baseline_up_rate: number | null;
      }
    | undefined;
  return {
    tickersScored: row?.tickers_scored ?? 0,
    hitsTotal: row?.hits_total ?? 0,
    signalsTotal: row?.signals_total ?? 0,
    baselineUpRate: row?.baseline_up_rate ?? null,
  };
}

/** Coverage classification for a set of tickers against the scan universe. */
export interface CoverageResult {
  covered: string[];
  inactive: string[];
  uncovered: string[];
}

interface SymbolRow {
  ticker: string;
  active: boolean;
}

/**
 * One cheap query classifying each ticker as covered (active in the scan
 * universe), inactive (a known symbol, but `active = false` — signals may be
 * stale), or uncovered (not in `symbols` at all — will always render blank).
 *
 * @param tickers - Uppercased ticker symbols.
 * @throws ApiError if Supabase isn't configured or the query fails.
 */
export async function fetchCoverage(tickers: string[]): Promise<CoverageResult> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured");
  }
  const covered: string[] = [];
  const inactive: string[] = [];
  const known = new Set<string>();

  for (const batch of chunk(tickers, IN_CHUNK)) {
    const { data, error } = await supabase
      .from("symbols")
      .select("ticker,active")
      .in("ticker", batch);
    if (error) throw new ApiError(500, error.message);
    for (const row of (data ?? []) as SymbolRow[]) {
      known.add(row.ticker);
      if (row.active) covered.push(row.ticker);
      else inactive.push(row.ticker);
    }
  }
  const uncovered = tickers.filter((t) => !known.has(t));
  return { covered, inactive, uncovered };
}

/**
 * Fetch the latest published signal for a ticker/period from Supabase.
 *
 * Reads go straight to Supabase from the browser — see
 * docs/backend-state-and-supabase-plan.md Part 2. There is no live
 * per-request computation anymore: signals are only as fresh as the last
 * scheduled scan, and only exist for tickers/periods the scanner has
 * actually covered.
 *
 * @param symbol - Ticker symbol (e.g. "AAPL"), will be uppercased.
 * @param period - Analysis period string (e.g. "3mo").
 * @param _noLlm - Unused: LLM synthesis is decided by the scanner, not the
 *   reader. Kept for call-site compatibility during the FastAPI->Supabase
 *   transition.
 * @throws SignalNotFoundError if no row exists for this ticker/period yet.
 * @throws ApiError if Supabase isn't configured or the query fails.
 */
export async function fetchSignal(
  symbol: string,
  period: string,
  _noLlm: boolean
): Promise<SignalOutput> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured (missing NEXT_PUBLIC_SUPABASE_URL/ANON_KEY)");
  }

  const ticker = symbol.toUpperCase();
  const { data, error } = await supabase
    .from("signals")
    .select(SIGNAL_ROW_COLUMNS)
    .eq("ticker", ticker)
    .eq("period", period)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) {
    throw new ApiError(500, error.message);
  }
  if (!data) {
    throw new SignalNotFoundError(ticker, period);
  }

  return rowToSignalOutput(data as unknown as SignalRow, period);
}

/** Latest engine-run health, for the site-wide status strip (§5 item #9). */
export interface EngineHealth {
  status: string;
  symbolsTotal: number;
  symbolsOk: number;
  symbolsFailed: number;
  finishedAt: string | null;
  startedAt: string;
  /** True when the newest run failed/partial, or finished > STALE_HOURS ago,
   * or is still "running" well past when it should have finished. */
  degraded: boolean;
  ageHours: number | null;
}

interface EngineRunRow {
  status: string;
  symbols_total: number;
  symbols_ok: number;
  symbols_failed: number;
  finished_at: string | null;
  started_at: string;
}

const ENGINE_STALE_HOURS = 26;

/**
 * Read the newest `engine_runs` row. Returns null when Supabase isn't
 * configured or there are no runs yet (never throws — a status strip must
 * not take the page down).
 */
export async function fetchEngineHealth(): Promise<EngineHealth | null> {
  if (!supabaseConfigured || !supabase) return null;
  try {
    const { data, error } = await supabase
      .from("engine_runs")
      .select(
        "status,symbols_total,symbols_ok,symbols_failed,finished_at,started_at",
      )
      .order("started_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error || !data) return null;

    const row = data as unknown as EngineRunRow;
    const refTs = row.finished_at
      ? new Date(row.finished_at).getTime()
      : new Date(row.started_at).getTime();
    const ageHours = (Date.now() - refTs) / (60 * 60 * 1000);
    const degraded =
      row.status === "failed" ||
      row.status === "partial" ||
      ageHours > ENGINE_STALE_HOURS;

    return {
      status: row.status,
      symbolsTotal: row.symbols_total,
      symbolsOk: row.symbols_ok,
      symbolsFailed: row.symbols_failed,
      finishedAt: row.finished_at,
      startedAt: row.started_at,
      degraded,
      ageHours: Number.isFinite(ageHours) ? ageHours : null,
    };
  } catch {
    return null;
  }
}

/* ────────────────────────────────────────────────────────────────────────
 * Coverage requests (§5 item #15) — queue an uncovered ticker for the
 * operator to add to the scan universe. Insert-own + read-own; requires a
 * signed-in session (RLS is `auth.uid() = user_id`).
 * ──────────────────────────────────────────────────────────────────────── */

export interface CoverageRequest {
  ticker: string;
  note: string;
  status: string;
  requestedAt: string;
}

/** All coverage requests the current user has filed, keyed by ticker. */
export async function fetchMyCoverageRequests(): Promise<
  Map<string, CoverageRequest>
> {
  const out = new Map<string, CoverageRequest>();
  if (!supabaseConfigured || !supabase) return out;
  const { data, error } = await supabase
    .from("coverage_requests")
    .select("ticker,note,status,requested_at");
  if (error || !data) return out;
  for (const r of data as Array<{
    ticker: string;
    note: string;
    status: string;
    requested_at: string;
  }>) {
    out.set(r.ticker, {
      ticker: r.ticker,
      note: r.note,
      status: r.status,
      requestedAt: r.requested_at,
    });
  }
  return out;
}

/**
 * Queue a ticker for scan coverage. Idempotent via the (user_id, ticker)
 * unique constraint — a repeat call is a no-op upsert.
 *
 * @throws ApiError(401) when not signed in.
 */
export async function requestCoverage(
  ticker: string,
  note = "",
): Promise<void> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured");
  }
  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) {
    throw new ApiError(401, "Sign in to request coverage for a ticker");
  }
  const { error } = await supabase.from("coverage_requests").upsert(
    { user_id: auth.user.id, ticker: ticker.toUpperCase(), note },
    { onConflict: "user_id,ticker" },
  );
  if (error) throw new ApiError(500, error.message);
}

/**
 * Check whether Supabase is reachable and configured.
 *
 * @returns true if a lightweight query against `symbols` succeeds.
 */
export async function checkHealth(): Promise<boolean> {
  if (!supabaseConfigured || !supabase) return false;
  try {
    const { error } = await supabase.from("symbols").select("ticker").limit(1);
    return !error;
  } catch {
    return false;
  }
}
