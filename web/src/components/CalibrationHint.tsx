"use client";

/**
 * Item #6 — calibration-aware confidence. Next to the confidence number,
 * show the measured historical hit-rate for this strength bucket from the
 * public `calibration` table: "0.72 confidence · this bucket has hit 61% of
 * the time (n=1,204)". Closes the gap the backtest engine's own docstring
 * names — a HIGH confidence label should mean a measurably higher hit-rate.
 */
import { useEffect, useState } from "react";
import {
  calibrationForDirection,
  formatCalibration,
  type CalibrationBucket,
} from "@/lib/calibration";
import type { SignalDirection } from "@/lib/types";

interface CalibrationHintProps {
  direction: SignalDirection;
}

export function CalibrationHint({ direction }: CalibrationHintProps) {
  const [bucket, setBucket] = useState<CalibrationBucket | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let active = true;
    calibrationForDirection(direction)
      .then((b) => active && setBucket(b))
      .finally(() => active && setDone(true));
    return () => {
      active = false;
    };
  }, [direction]);

  if (!done || !bucket) return null;

  const thin = bucket.total < 30;
  return (
    <span
      className="text-[11px] text-gray-500"
      title={`Strength bucket ${bucket.bucketKey}, ${bucket.horizonDays}d horizon, engine ${bucket.codeVersion}`}
    >
      · {formatCalibration(bucket)}
      {thin && <span className="text-amber-600"> — thin sample</span>}
    </span>
  );
}
