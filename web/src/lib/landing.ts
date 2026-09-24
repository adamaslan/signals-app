/** Pure helpers for the landing-page showcase. */
import type { LandingSignal } from "./api";

/** Share of published signals with degraded LLM synthesis that trips the banner. */
export const AI_DEGRADED_BANNER_SHARE = 0.2;

/**
 * Absolute timestamp in US Eastern, matching the deep-dive convention
 * ("Sep 19, 16:00 ET"). Returns null for missing/invalid input.
 */
export function formatEt(ts: string | number | null | undefined): string | null {
  if (ts == null) return null;
  const ms = typeof ts === "string" ? new Date(ts).getTime() : ts;
  if (!Number.isFinite(ms)) return null;
  const text = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(ms));
  return `${text} ET`;
}

/** Fraction of signals whose LLM synthesis ran degraded (0 when empty). */
export function aiDegradedShare(signals: LandingSignal[]): number {
  if (signals.length === 0) return 0;
  return signals.filter((s) => s.aiDegraded).length / signals.length;
}

/** Newest `barTs` across signals, as an ISO string (null when none). */
export function newestBarTs(signals: LandingSignal[]): string | null {
  let best: number | null = null;
  let bestIso: string | null = null;
  for (const s of signals) {
    if (!s.barTs) continue;
    const t = new Date(s.barTs).getTime();
    if (Number.isFinite(t) && (best == null || t > best)) {
      best = t;
      bestIso = s.barTs;
    }
  }
  return bestIso;
}

/** Highest-confidence non-hold signal — the automatic featured deep dive. */
export function pickFeatured(signals: LandingSignal[]): LandingSignal | null {
  let best: LandingSignal | null = null;
  for (const s of signals) {
    if (s.direction === "hold") continue;
    if (!best || (s.confidence ?? -1) > (best.confidence ?? -1)) best = s;
  }
  return best;
}

const DIRECTION_ORDER: Record<string, number> = {
  strong_buy: 0,
  buy: 1,
  hold: 2,
  sell: 3,
  strong_sell: 4,
};

/** Group-by-direction (bullish → bearish), then confidence desc, for the heatmap. */
export function sortForHeatmap(signals: LandingSignal[]): LandingSignal[] {
  return [...signals].sort(
    (a, b) =>
      (DIRECTION_ORDER[a.direction] ?? 9) - (DIRECTION_ORDER[b.direction] ?? 9) ||
      (b.confidence ?? -1) - (a.confidence ?? -1) ||
      a.ticker.localeCompare(b.ticker),
  );
}
