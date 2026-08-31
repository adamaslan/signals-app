import { describe, it, expect } from "vitest";
import {
  wilsonLowerBound,
  wilsonUpperBound,
  toHitRateBucket,
  THIN_BUCKET_N,
} from "./stats";

describe("wilsonLowerBound", () => {
  it("returns 0 for zero total", () => {
    expect(wilsonLowerBound(0, 0)).toBe(0);
    expect(wilsonLowerBound(5, 0)).toBe(0);
  });

  it("returns a value between 0.35 and 0.50 for 3/3 (not 1.0)", () => {
    const lower = wilsonLowerBound(3, 3);
    expect(lower).toBeGreaterThan(0.35);
    expect(lower).toBeLessThan(0.5);
  });

  it("returns a value between 0.4 and 0.5 for 50/100", () => {
    const lower = wilsonLowerBound(50, 100);
    expect(lower).toBeGreaterThan(0.4);
    expect(lower).toBeLessThan(0.5);
  });
});

describe("wilsonUpperBound", () => {
  it("returns 0 for zero total", () => {
    expect(wilsonUpperBound(0, 0)).toBe(0);
  });

  it("returns a value between 0 and 0.7 for 0/3 (non-trivial upper bound)", () => {
    const upper = wilsonUpperBound(0, 3);
    expect(upper).toBeGreaterThan(0);
    expect(upper).toBeLessThan(0.7);
  });

  it("returns a value between 0.5 and 0.6 for 50/100", () => {
    const upper = wilsonUpperBound(50, 100);
    expect(upper).toBeGreaterThan(0.5);
    expect(upper).toBeLessThan(0.6);
  });
});

describe("Wilson bounds consistency", () => {
  it("lower < hitRate < upper for 40/100", () => {
    const hits = 40;
    const total = 100;
    const hitRate = hits / total;
    const lower = wilsonLowerBound(hits, total);
    const upper = wilsonUpperBound(hits, total);

    expect(lower).toBeLessThan(hitRate);
    expect(hitRate).toBeLessThan(upper);
  });
});

describe("toHitRateBucket", () => {
  it("returns correct structure for BULLISH 12/20 (thin bucket)", () => {
    const bucket = toHitRateBucket("BULLISH", 12, 20);

    expect(bucket.key).toBe("BULLISH");
    expect(bucket.hits).toBe(12);
    expect(bucket.total).toBe(20);
    expect(bucket.hitRate).toBe(0.6);
    expect(bucket.thin).toBe(true); // 20 < 30 (THIN_BUCKET_N)
    expect(bucket.hitRateLower).toBeLessThan(0.6);
    expect(bucket.hitRateUpper).toBeGreaterThan(0.6);
  });

  it("returns thin=false for 300/500 (not thin)", () => {
    const bucket = toHitRateBucket("x", 300, 500);

    expect(bucket.thin).toBe(false); // 500 >= 30
  });
});
