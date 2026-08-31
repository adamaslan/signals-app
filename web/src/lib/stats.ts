/**
 * Small statistics helpers for backtest hit-rate reporting (spec §5 #18).
 * Kept framework-free so both `universe.ts` and the backtest panel use them.
 */

/**
 * Wilson score interval lower bound at 95% confidence (z = 1.96).
 *
 * The honest number for a hit rate: a 3/3 bucket has a true 95% lower bound
 * near 0.44, not 1.0. Use this — not the raw hit rate — for any bar a user
 * might act on.
 */
export function wilsonLowerBound(hits: number, total: number): number {
  if (total <= 0) return 0;
  const z = 1.96;
  const p = hits / total;
  const denom = 1 + (z * z) / total;
  const centre = p + (z * z) / (2 * total);
  const margin = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * total)) / total);
  return Math.max(0, (centre - margin) / denom);
}

/** Wilson score interval UPPER bound at 95% (z = 1.96). */
export function wilsonUpperBound(hits: number, total: number): number {
  if (total <= 0) return 0;
  const z = 1.96;
  const p = hits / total;
  const denom = 1 + (z * z) / total;
  const centre = p + (z * z) / (2 * total);
  const margin = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * total)) / total);
  return Math.min(1, (centre + margin) / denom);
}

/** A hit-rate bucket with its honest (Wilson-bounded) interval attached. */
export interface HitRateBucket {
  key: string;
  hits: number;
  total: number;
  hitRate: number;
  hitRateLower: number;
  hitRateUpper: number;
  /** Below this n, the bucket is statistically too thin to trust. */
  thin: boolean;
}

export const THIN_BUCKET_N = 30;

export function toHitRateBucket(
  key: string,
  hits: number,
  total: number,
): HitRateBucket {
  return {
    key,
    hits,
    total,
    hitRate: total > 0 ? hits / total : 0,
    hitRateLower: wilsonLowerBound(hits, total),
    hitRateUpper: wilsonUpperBound(hits, total),
    thin: total < THIN_BUCKET_N,
  };
}
