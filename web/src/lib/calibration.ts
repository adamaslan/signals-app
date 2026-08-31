import { supabase, supabaseConfigured } from "./supabase";
import type { SignalDirection } from "./types";

/** Calibration bucket from the public `calibration` table. */
export interface CalibrationBucket {
  bucketKind: string;
  bucketKey: string;
  horizonDays: number;
  hits: number;
  total: number;
  hitRate: number;
  codeVersion: string;
}

/** Module-level cache. */
let cache: CalibrationBucket[] | null = null;
let inflight: Promise<CalibrationBucket[]> | null = null;

/**
 * Load calibration buckets from Supabase, with caching.
 * Returns cached result if available; otherwise fetches active rows.
 * @returns Array of calibration buckets, or empty array on error.
 */
export async function loadCalibration(): Promise<CalibrationBucket[]> {
  if (cache) {
    return cache;
  }

  if (inflight) {
    return inflight;
  }

  inflight = (async () => {
    try {
      if (!supabaseConfigured || !supabase) {
        return [];
      }

      const { data, error } = await supabase
        .from("calibration")
        .select(
          "bucket_kind,bucket_key,horizon_days,hits,total,hit_rate,code_version"
        )
        .eq("is_active", true);

      if (error) {
        return [];
      }

      type Row = {
        bucket_kind: string;
        bucket_key: string;
        horizon_days: number;
        hits: number;
        total: number;
        hit_rate: number;
        code_version: string;
      };
      const rows: CalibrationBucket[] = ((data ?? []) as Row[]).map((row) => ({
        bucketKind: row.bucket_kind,
        bucketKey: row.bucket_key,
        horizonDays: row.horizon_days,
        hits: row.hits,
        total: row.total,
        hitRate: row.hit_rate,
        codeVersion: row.code_version,
      }));

      cache = rows;
      return rows;
    } catch {
      return [];
    } finally {
      inflight = null;
    }
  })();

  return inflight;
}

/** Clear the in-memory calibration cache. */
export function clearCalibrationCache(): void {
  cache = null;
  inflight = null;
}

/** Map from SignalDirection to the strength bucket key used in the calibration table. */
export const DIRECTION_TO_STRENGTH_KEY: Record<SignalDirection, string> = {
  strong_buy: "STRONG_BULLISH",
  buy: "BULLISH",
  hold: "NEUTRAL",
  sell: "BEARISH",
  strong_sell: "STRONG_BEARISH",
};

/**
 * Fetch calibration for a signal direction.
 * Filters to strength bucket matching the direction.
 * If horizonDays given, prefer exact match; otherwise pick highest total.
 * @param direction Signal direction (e.g., "strong_buy").
 * @param opts Optional filters (e.g., horizonDays).
 * @returns Matching calibration bucket, or null.
 */
export async function calibrationForDirection(
  direction: SignalDirection,
  opts?: { horizonDays?: number }
): Promise<CalibrationBucket | null> {
  const rows = await loadCalibration();

  const strengthKey = DIRECTION_TO_STRENGTH_KEY[direction];
  const filtered = rows.filter(
    (r) => r.bucketKind === "strength" && r.bucketKey === strengthKey
  );

  if (filtered.length === 0) {
    return null;
  }

  if (opts?.horizonDays !== undefined) {
    const exact = filtered.find((r) => r.horizonDays === opts.horizonDays);
    if (exact) {
      return exact;
    }
  }

  return filtered.reduce((best, current) =>
    current.total > best.total ? current : best
  );
}

/**
 * Format a calibration bucket for display.
 * @param b Calibration bucket.
 * @returns Human-readable string (e.g., "this bucket has hit 61% of the time (n=1,204)").
 */
export function formatCalibration(b: CalibrationBucket): string {
  const percentage = Math.round(b.hitRate * 100);
  const count = b.total.toLocaleString();
  return `this bucket has hit ${percentage}% of the time (n=${count})`;
}
