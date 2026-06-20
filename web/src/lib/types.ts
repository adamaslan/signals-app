/**
 * TypeScript types mirroring the Python Pydantic schemas in
 * src/signals_app/schemas/signal_output.py
 */

export type SignalDirection =
  | "strong_buy"
  | "buy"
  | "hold"
  | "sell"
  | "strong_sell";

export type Timeframe = "1D" | "5D" | "1M" | "3M" | "6M" | "1Y";

export type EvidenceSource =
  | "technical"
  | "fundamental"
  | "macro"
  | "news_sentiment"
  | "options_flow"
  | "sector_relative"
  | "cross_asset"
  | "earnings"
  | "rule_based";

export type DivergencePattern =
  | "aligned_bullish"
  | "aligned_bearish"
  | "short_bull_long_bear"
  | "short_bear_long_bull"
  | "mixed"
  | "insufficient_data";

export interface EvidenceItem {
  source: EvidenceSource;
  weight: number;
  summary: string;
  is_counter: boolean;
}

export interface Evidence {
  items: EvidenceItem[];
}

export interface Signal {
  direction: SignalDirection;
  confidence: number;
  timeframe: Timeframe;
  evidence: Evidence;
  ai_degraded: boolean;
  prompt_version: string;
}

export interface TimeframeMatrix {
  ticker: string;
  /** Map of timeframe string (e.g. "1D") to Signal */
  signals: Record<string, Signal>;
  alignment_score: number;
  divergence_pattern: DivergencePattern;
  divergence_interpretation: string;
  computed_at: string;
}

export interface SignalOutput {
  ticker: string;
  signal: Signal;
  matrix: TimeframeMatrix | null;
  feature_unavailable: string[];
  schema_version: string;
}

/** Periods the backend currently accepts end-to-end. */
export const VALID_PERIODS = ["1d", "5d", "1mo", "3mo", "6mo", "1y"] as const;
export type Period = (typeof VALID_PERIODS)[number];

export const PERIOD_LABELS: Record<Period, string> = {
  "1d": "1D",
  "5d": "5D",
  "1mo": "1M",
  "3mo": "3M",
  "6mo": "6M",
  "1y": "1Y",
};

/**
 * The full range of period controls offered in the UI, ordered shortest
 * (1 minute) to longest (lifetime/max). Each entry knows whether the backend
 * supports it today; unsupported entries are mapped to the nearest supported
 * period via `supportedFallback` so the app never breaks.
 *
 * When the backend later gains intraday + long-range support, flip `supported`
 * to true and drop the fallback — no frontend rewrite needed.
 */
export interface PeriodOption {
  /** Key sent to / mapped for the backend (raw UI value). */
  id: string;
  /** Short label shown on chips / slider ticks. */
  label: string;
  /** Longer label for tooltips / menus. */
  longLabel: string;
  /** Approximate calendar span in minutes, used for slider ordering. */
  spanMinutes: number;
  /** True when the backend can serve this period directly. */
  supported: boolean;
  /** Nearest supported Period used when `supported` is false. */
  supportedFallback: Period;
  /** Grouping for the UI (intraday vs daily-plus). */
  group: "intraday" | "swing" | "long";
}

const MIN = 1;
const HOUR = 60;
const DAY = 60 * 24;

export const PERIOD_OPTIONS: PeriodOption[] = [
  { id: "1m",   label: "1m",  longLabel: "1 minute",   spanMinutes: 1 * MIN,        supported: false, supportedFallback: "1d",  group: "intraday" },
  { id: "5m",   label: "5m",  longLabel: "5 minutes",  spanMinutes: 5 * MIN,        supported: false, supportedFallback: "1d",  group: "intraday" },
  { id: "15m",  label: "15m", longLabel: "15 minutes", spanMinutes: 15 * MIN,       supported: false, supportedFallback: "1d",  group: "intraday" },
  { id: "1h",   label: "1h",  longLabel: "1 hour",     spanMinutes: 1 * HOUR,       supported: false, supportedFallback: "1d",  group: "intraday" },
  { id: "4h",   label: "4h",  longLabel: "4 hours",    spanMinutes: 4 * HOUR,       supported: false, supportedFallback: "5d",  group: "intraday" },
  { id: "1d",   label: "1D",  longLabel: "1 day",      spanMinutes: 1 * DAY,        supported: true,  supportedFallback: "1d",  group: "swing" },
  { id: "5d",   label: "5D",  longLabel: "5 days",     spanMinutes: 5 * DAY,        supported: true,  supportedFallback: "5d",  group: "swing" },
  { id: "1mo",  label: "1M",  longLabel: "1 month",    spanMinutes: 30 * DAY,       supported: true,  supportedFallback: "1mo", group: "swing" },
  { id: "3mo",  label: "3M",  longLabel: "3 months",   spanMinutes: 90 * DAY,       supported: true,  supportedFallback: "3mo", group: "swing" },
  { id: "6mo",  label: "6M",  longLabel: "6 months",   spanMinutes: 180 * DAY,      supported: true,  supportedFallback: "6mo", group: "long" },
  { id: "1y",   label: "1Y",  longLabel: "1 year",     spanMinutes: 365 * DAY,      supported: true,  supportedFallback: "1y",  group: "long" },
  { id: "5y",   label: "5Y",  longLabel: "5 years",    spanMinutes: 5 * 365 * DAY,  supported: false, supportedFallback: "1y",  group: "long" },
  { id: "max",  label: "MAX", longLabel: "Lifetime",   spanMinutes: 50 * 365 * DAY, supported: false, supportedFallback: "1y",  group: "long" },
];

const PERIOD_OPTION_BY_ID: Record<string, PeriodOption> = Object.fromEntries(
  PERIOD_OPTIONS.map((o) => [o.id, o]),
);

export function getPeriodOption(id: string): PeriodOption | undefined {
  return PERIOD_OPTION_BY_ID[id];
}

/**
 * Resolve any UI period id to a backend-safe Period. Supported ids pass
 * through; unsupported ones fall back to their nearest supported neighbour.
 */
export function resolveBackendPeriod(id: string): Period {
  const opt = PERIOD_OPTION_BY_ID[id];
  if (!opt) return "3mo";
  return opt.supported ? (opt.id as Period) : opt.supportedFallback;
}

export const SIGNAL_COLORS: Record<SignalDirection, string> = {
  strong_buy: "#00C853",
  buy: "#69F0AE",
  hold: "#FFD740",
  sell: "#FF6D00",
  strong_sell: "#D50000",
};

export const SIGNAL_LABELS: Record<SignalDirection, string> = {
  strong_buy: "STRONG BUY",
  buy: "BUY",
  hold: "HOLD",
  sell: "SELL",
  strong_sell: "STRONG SELL",
};

export const SIGNAL_ARROWS: Record<SignalDirection, string> = {
  strong_buy: "⬆",
  buy: "↑",
  hold: "→",
  sell: "↓",
  strong_sell: "⬇",
};

export const TIMEFRAMES: Timeframe[] = ["1D", "5D", "1M", "3M", "6M", "1Y"];
