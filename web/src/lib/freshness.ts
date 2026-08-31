/**
 * Signal freshness classification (spec item #1 — the highest-priority
 * viewing fix). Under scan-then-read, a signal card looks identical whether
 * it was computed 20 minutes ago or 9 days ago during a broken cron run.
 * This turns the age of `bar_ts` into an explicit state.
 */

export type Freshness = "fresh" | "stale" | "very-stale" | "unknown";

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export interface FreshnessInfo {
  level: Freshness;
  /** Whole days between `barTs` and now (null when unknown). */
  ageDays: number | null;
  label: string;
}

/**
 * Classify by the age of the bar the signal describes:
 * - fresh: < 1 calendar day
 * - stale: 1–3 days
 * - very-stale: > 3 days
 * - unknown: no bar timestamp on the row
 *
 * Calendar days, not trading days — deliberately conservative, so a Friday
 * signal read on Monday reads "stale (3d)" rather than being hand-waved as
 * "1 trading day".
 */
export function classifyFreshness(
  barTs: number | string | null | undefined,
  now: number = Date.now(),
): FreshnessInfo {
  if (barTs == null) {
    return { level: "unknown", ageDays: null, label: "Age unknown" };
  }
  const ts = typeof barTs === "string" ? new Date(barTs).getTime() : barTs;
  if (!Number.isFinite(ts)) {
    return { level: "unknown", ageDays: null, label: "Age unknown" };
  }
  const ageDays = Math.floor((now - ts) / ONE_DAY_MS);
  if (ageDays < 1) return { level: "fresh", ageDays, label: "Fresh" };
  if (ageDays <= 3) {
    return { level: "stale", ageDays, label: `Stale · ${ageDays}d` };
  }
  return { level: "very-stale", ageDays, label: `Very stale · ${ageDays}d` };
}

export const FRESHNESS_COLORS: Record<Freshness, string> = {
  fresh: "#00C853",
  stale: "#FFD740",
  "very-stale": "#D50000",
  unknown: "#666",
};
