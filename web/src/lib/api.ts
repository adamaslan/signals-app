import { supabase, supabaseConfigured } from "./supabase";
import type { EvidenceItem, Signal, SignalDirection, SignalOutput, Timeframe } from "./types";

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
  direction: string | null;
  confidence: number | null;
  bias: string;
  bull_count: number;
  bear_count: number;
  total_signals: number;
  data_quality_score: number | null;
  data_quality_reasons: string[];
  evidence: EvidenceItem[];
  counter_evidence: EvidenceItem[];
  matrix: unknown | null;
  ai_degraded: boolean;
  no_llm: boolean;
  prompt_version: string | null;
  code_version: string;
  created_at: string;
}

const SIGNAL_ROW_COLUMNS =
  "ticker,period,direction,confidence,bias,bull_count,bear_count,total_signals," +
  "data_quality_score,data_quality_reasons,evidence,counter_evidence,matrix," +
  "ai_degraded,no_llm,prompt_version,code_version,created_at";

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
    matrix: null, // wired in Phase 10
    feature_unavailable: row.no_llm ? ["llm_synthesis"] : [],
    schema_version: "1.0",
    code_version: row.code_version,
    data_quality_score: row.data_quality_score,
    data_quality_reasons: row.data_quality_reasons ?? [],
  };
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
