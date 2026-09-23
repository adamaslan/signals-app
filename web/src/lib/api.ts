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
const CHUNK_TIMEOUT_MS = 15_000;

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** A chunk's Supabase read exceeded {@link CHUNK_TIMEOUT_MS}. Distinct from
 * ApiError so callers (and the UI) can tell a stalled read apart from a real
 * query failure — the message already names which chunk stalled. */
export class ChunkTimeoutError extends ApiError {
  constructor(label: string) {
    super(408, `Timed out after ${CHUNK_TIMEOUT_MS / 1000}s reading ${label}`);
    this.name = "ChunkTimeoutError";
  }
}

/** Race `promise` against a timeout, rejecting with a {@link ChunkTimeoutError}
 * named for `label` if it fires first. Always clears the timer. */
async function withTimeout<T>(
  promise: PromiseLike<T>,
  ms: number,
  label: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new ChunkTimeoutError(label)), ms);
  });
  try {
    return await Promise.race([Promise.resolve(promise), timeout]);
  } finally {
    clearTimeout(timer!);
  }
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
 * @param opts.signal - Aborts the in-flight chunk and stops reading further
 *   chunks. Pass an `AbortSignal` tied to the caller's unmount/cancel.
 * @param opts.onProgress - Called after each chunk with `(done, total)`
 *   ticker counts, so the UI can show "Reading 600/954…" instead of a bare
 *   spinner.
 * @throws ApiError if Supabase isn't configured or a query fails.
 * @throws ChunkTimeoutError if a chunk's read exceeds {@link CHUNK_TIMEOUT_MS}.
 */
export async function fetchUniverseSignals(
  tickers: string[],
  period: string,
  opts: {
    signal?: AbortSignal;
    onProgress?: (done: number, total: number) => void;
  } = {},
): Promise<Map<string, UniverseSignalSnapshot>> {
  if (!supabaseConfigured || !supabase) {
    throw new ApiError(503, "Supabase is not configured");
  }
  const newest = new Map<string, UniverseSignalSnapshot>();
  if (tickers.length === 0) return newest;

  const chunks = chunk(tickers, IN_CHUNK);
  for (let i = 0; i < chunks.length; i++) {
    if (opts.signal?.aborted) {
      throw new ApiError(499, "Universe read cancelled");
    }
    const batch = chunks[i];
    const label = `chunk ${i + 1}/${chunks.length} (tickers ${batch[0]}…${batch[batch.length - 1]})`;

    // `latest_signals` (migration 20260831000001) is DISTINCT ON (ticker,
    // period) — one indexed row per ticker, no client-side de-dup. Falls
    // back to the raw `signals` table + newest-wins loop if the view is
    // missing (older DB), so a not-yet-migrated environment still works.
    let rows: UniverseSignalRow[] | null = null;
    let viewQuery = supabase
      .from("latest_signals")
      .select(UNIVERSE_SIGNAL_COLUMNS)
      .in("ticker", batch)
      .eq("period", period);
    if (opts.signal) viewQuery = viewQuery.abortSignal(opts.signal);
    const viewRes = await withTimeout(viewQuery, CHUNK_TIMEOUT_MS, label);
    if (!viewRes.error) {
      rows = (viewRes.data ?? []) as unknown as UniverseSignalRow[];
    } else {
      let rawQuery = supabase
        .from("signals")
        .select(UNIVERSE_SIGNAL_COLUMNS)
        .in("ticker", batch)
        .eq("period", period)
        .order("bar_ts", { ascending: false, nullsFirst: false });
      if (opts.signal) rawQuery = rawQuery.abortSignal(opts.signal);
      const rawRes = await withTimeout(rawQuery, CHUNK_TIMEOUT_MS, label);
      if (rawRes.error) throw new ApiError(500, rawRes.error.message);
      rows = (rawRes.data ?? []) as unknown as UniverseSignalRow[];
    }

    for (const row of rows) {
      // From the view each ticker appears once; from the raw fallback the
      // first (newest by bar_ts) wins.
      if (!newest.has(row.ticker)) newest.set(row.ticker, rowToSnapshot(row));
    }
    opts.onProgress?.(Math.min((i + 1) * IN_CHUNK, tickers.length), tickers.length);
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

/* ────────────────────────────────────────────────────────────────────────
 * Local backend scan trigger — POST /scan via the `/api/*` dev-only rewrite
 * (next.config.ts) to the FastAPI server on :8000 (scripts/run_local.sh).
 * Not available on the deployed static site — only meaningful when both the
 * Next dev server and the local Python backend are running side by side.
 * ──────────────────────────────────────────────────────────────────────── */

export interface ScanOutcome {
  ticker: string;
  ok: boolean;
  published: boolean;
  reason: string | null;
}

export interface ScanResponse {
  symbolsTotal: number;
  symbolsOk: number;
  symbolsFailed: number;
  symbolsPublished: number;
  dryRun: boolean;
  trigger: string;
  elapsedSeconds: number;
  outcomes: ScanOutcome[];
}

interface ScanResponseRow {
  symbols_total: number;
  symbols_ok: number;
  symbols_failed: number;
  symbols_published: number;
  dry_run: boolean;
  trigger: string;
  elapsed_seconds: number;
  outcomes: ScanOutcome[];
}

/** Mirrors `signals_app.config.MAX_MANUAL_SCAN_SYMBOLS` — kept in sync by
 * hand since the frontend has no import path into the Python package. */
export const MAX_MANUAL_SCAN_SYMBOLS = 100;

export interface TriggerScanOpts {
  period?: string;
  dryRun?: boolean;
  computeMatrix?: boolean;
}

/**
 * Trigger a real, synchronous scan for `tickers` against the local backend
 * (`POST /api/scan` -> FastAPI `POST /scan` -> `signals_app.service.scan`,
 * `trigger="manual"`). Publishes straight to Supabase on success, so a
 * subsequent `runUniverse` read picks up fresh rows.
 *
 * @throws ApiError(503) if the local backend isn't reachable — the caller
 *   should tell the user to start `scripts/run_local.sh`.
 * @throws ApiError with the backend's own status/detail for a validation or
 *   upstream failure (bad period, no Supabase writer configured, etc).
 */
export async function triggerUniverseScan(
  tickers: string[],
  opts: TriggerScanOpts = {},
): Promise<ScanResponse> {
  if (tickers.length === 0) {
    throw new ApiError(400, "No tickers to scan");
  }
  let res: Response;
  try {
    res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbols: tickers,
        period: opts.period,
        dry_run: opts.dryRun ?? false,
        compute_matrix: opts.computeMatrix ?? false,
      }),
    });
  } catch {
    throw new ApiError(
      503,
      "Local backend not reachable at /api/scan — start it with " +
        "`scripts/run_local.sh` (needs `next dev`, not the static export).",
    );
  }
  const body = (await res.json().catch(() => null)) as
    | ScanResponseRow
    | { detail?: string }
    | null;
  if (!res.ok) {
    const detail =
      body && "detail" in body && body.detail ? body.detail : res.statusText;
    throw new ApiError(res.status, `Scan failed: ${detail}`);
  }
  const row = body as ScanResponseRow;
  return {
    symbolsTotal: row.symbols_total,
    symbolsOk: row.symbols_ok,
    symbolsFailed: row.symbols_failed,
    symbolsPublished: row.symbols_published,
    dryRun: row.dry_run,
    trigger: row.trigger,
    elapsedSeconds: row.elapsed_seconds,
    outcomes: row.outcomes,
  };
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

/* ────────────────────────────────────────────────────────────────────────
 * Landing-page showcase reads. Every function here returns `null` (never
 * throws) when Supabase is unset or a query fails — a showcase section must
 * degrade to its empty state, not take the page down.
 * ──────────────────────────────────────────────────────────────────────── */

/** Period that drives every landing-page section. */
export const LANDING_PERIOD = "3mo";

/** Supabase's default row cap; the universe is ~954 tickers so one page fits. */
const LANDING_ROW_LIMIT = 1000;

const LANDING_COLUMNS =
  "ticker,direction,confidence,confluence_score,data_quality_score,ai_degraded,bar_ts,created_at";

export interface LandingSignal {
  ticker: string;
  direction: SignalDirection;
  confidence: number | null;
  confluenceScore: number | null;
  dataQuality: number | null;
  aiDegraded: boolean;
  barTs: string | null;
  createdAt: string | null;
}

interface LandingRow {
  ticker: string;
  direction: string | null;
  confidence: number | null;
  confluence_score: number | null;
  data_quality_score: number | null;
  ai_degraded: boolean;
  bar_ts: string | null;
  created_at: string | null;
}

/**
 * Newest published signal per ticker for one period, light columns only
 * (no evidence JSONB). One query feeds the top-signals list, the heatmap
 * preview and the funnel's published count.
 */
export async function fetchLandingSignals(
  period: string = LANDING_PERIOD,
): Promise<LandingSignal[] | null> {
  if (!supabaseConfigured || !supabase) return null;
  try {
    let res = await supabase
      .from("latest_signals")
      .select(LANDING_COLUMNS)
      .eq("period", period)
      .limit(LANDING_ROW_LIMIT);
    if (res.error) {
      // Older DB without the view: fall back to the raw table (a superset of
      // rows; the newest-per-ticker de-dup below handles duplicates).
      res = await supabase
        .from("signals")
        .select(LANDING_COLUMNS)
        .eq("period", period)
        .order("bar_ts", { ascending: false, nullsFirst: false })
        .limit(LANDING_ROW_LIMIT);
      if (res.error) return null;
    }
    const seen = new Set<string>();
    const out: LandingSignal[] = [];
    for (const row of (res.data ?? []) as unknown as LandingRow[]) {
      if (seen.has(row.ticker) || !row.direction) continue;
      seen.add(row.ticker);
      out.push({
        ticker: row.ticker,
        direction: row.direction as SignalDirection,
        confidence: row.confidence,
        confluenceScore: row.confluence_score,
        dataQuality: row.data_quality_score,
        aiDegraded: row.ai_degraded,
        barTs: row.bar_ts,
        createdAt: row.created_at,
      });
    }
    return out;
  } catch {
    return null;
  }
}

export interface TopSignals {
  bullish: LandingSignal[];
  bearish: LandingSignal[];
}

/**
 * The strongest `perDirection` bullish and bearish published signals for a
 * period, ranked by confidence.
 */
export async function fetchTopSignals(
  period: string,
  perDirection: number,
): Promise<TopSignals | null> {
  const rows = await fetchLandingSignals(period);
  if (!rows) return null;
  return pickTopSignals(rows, perDirection);
}

/** Pure ranking half of {@link fetchTopSignals}, split out for testing. */
export function pickTopSignals(
  rows: LandingSignal[],
  perDirection: number,
): TopSignals {
  const rank = (a: LandingSignal, b: LandingSignal) =>
    (b.confidence ?? -1) - (a.confidence ?? -1) ||
    a.ticker.localeCompare(b.ticker);
  const bullish = rows
    .filter((r) => r.direction === "strong_buy" || r.direction === "buy")
    .sort(rank)
    .slice(0, perDirection);
  const bearish = rows
    .filter((r) => r.direction === "strong_sell" || r.direction === "sell")
    .sort(rank)
    .slice(0, perDirection);
  return { bullish, bearish };
}

export interface PipelineFunnel {
  /** Symbols the newest run attempted. */
  total: number;
  /** Symbols fetched + scored without error. */
  scanned: number;
  /** Symbols whose newest signal cleared the publication gate. */
  published: number;
  /** Scanned but rejected by the gate — these never reached the LLM. */
  gated: number;
  failed: number;
  finishedAt: string | null;
  startedAt: string;
}

/**
 * Stage counts for the newest engine run. `engine_runs` stores total/ok/failed
 * only, so `published` is the count of rows in `latest_signals` for the
 * period and `gated` is scanned − published (clamped at 0).
 */
export async function fetchPipelineFunnel(
  period: string = LANDING_PERIOD,
): Promise<PipelineFunnel | null> {
  if (!supabaseConfigured || !supabase) return null;
  try {
    const runRes = await supabase
      .from("engine_runs")
      .select("symbols_total,symbols_ok,symbols_failed,finished_at,started_at")
      .order("started_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (runRes.error || !runRes.data) return null;
    const run = runRes.data as unknown as {
      symbols_total: number;
      symbols_ok: number;
      symbols_failed: number;
      finished_at: string | null;
      started_at: string;
    };

    const countRes = await supabase
      .from("latest_signals")
      .select("ticker", { count: "exact", head: true })
      .eq("period", period);
    if (countRes.error || countRes.count == null) return null;

    return buildFunnel(run, countRes.count);
  } catch {
    return null;
  }
}

/** Pure half of {@link fetchPipelineFunnel}, split out for testing. */
export function buildFunnel(
  run: {
    symbols_total: number;
    symbols_ok: number;
    symbols_failed: number;
    finished_at: string | null;
    started_at: string;
  },
  publishedCount: number,
): PipelineFunnel {
  const published = Math.min(publishedCount, run.symbols_ok);
  return {
    total: run.symbols_total,
    scanned: run.symbols_ok,
    published,
    gated: Math.max(0, run.symbols_ok - published),
    failed: run.symbols_failed,
    finishedAt: run.finished_at,
    startedAt: run.started_at,
  };
}
