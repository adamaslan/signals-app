"use client";

import { useEffect, useState } from "react";
import { loadCalibration, type CalibrationBucket } from "@/lib/calibration";
import { toHitRateBucket } from "@/lib/stats";
import { EmptyState, Section, SkeletonBlock } from "./Shared";

const STRENGTH_ORDER: Array<[string, string]> = [
  ["STRONG_BULLISH", "Strong buy"],
  ["BULLISH", "Buy"],
  ["NEUTRAL", "Hold"],
  ["BEARISH", "Sell"],
  ["STRONG_BEARISH", "Strong sell"],
];

/** Per strength key, the horizon with the most samples. */
function bestPerKey(rows: CalibrationBucket[]): Map<string, CalibrationBucket> {
  const best = new Map<string, CalibrationBucket>();
  for (const r of rows) {
    if (r.bucketKind !== "strength") continue;
    const cur = best.get(r.bucketKey);
    if (!cur || r.total > cur.total) best.set(r.bucketKey, r);
  }
  return best;
}

export function TrackRecord() {
  const [rows, setRows] = useState<CalibrationBucket[] | null>(null);

  useEffect(() => {
    let active = true;
    loadCalibration().then((r) => active && setRows(r));
    return () => {
      active = false;
    };
  }, []);

  const best = rows ? bestPerKey(rows) : null;

  return (
    <Section testId="landing-track-record" title="Track record">
      {rows == null ? (
        <SkeletonBlock height="h-20" />
      ) : !best || best.size === 0 ? (
        <EmptyState>
          No calibration yet — hit rates appear after signals have realized forward returns.
        </EmptyState>
      ) : (
        <div className="space-y-2">
          <ul className="space-y-1">
            {STRENGTH_ORDER.filter(([k]) => best.has(k)).map(([key, label]) => {
              const row = best.get(key)!;
              const b = toHitRateBucket(key, row.hits, row.total);
              return (
                <li
                  key={key}
                  className={`flex items-center gap-3 text-sm ${b.thin ? "opacity-50" : ""}`}
                >
                  <span className="w-24 text-gray-300">{label}</span>
                  <span className="font-semibold text-white">
                    {Math.round(b.hitRate * 100)}%
                  </span>
                  <span className="text-xs text-gray-500">
                    ({Math.round(b.hitRateLower * 100)}–{Math.round(b.hitRateUpper * 100)}% ·{" "}
                    {row.horizonDays}d)
                  </span>
                  <span className="ml-auto text-xs text-gray-500">
                    n={b.total.toLocaleString()}
                    {b.thin && " · thin sample"}
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="text-[11px] text-gray-600">
            Hit rate with a 95% Wilson interval. Greyed rows have too few samples to trust.
          </p>
        </div>
      )}
    </Section>
  );
}
