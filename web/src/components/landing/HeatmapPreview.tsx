"use client";

import Link from "next/link";
import { useMemo } from "react";
import { sortForHeatmap } from "@/lib/landing";
import { SIGNAL_COLORS, SIGNAL_LABELS } from "@/lib/types";
import { useLandingData } from "./LandingData";
import { EmptyState, Section, SkeletonBlock } from "./Shared";

/** One 12px cell per published ticker (≤ ~1,000 DOM nodes), height-capped. */
export function HeatmapPreview() {
  const { loaded, signals } = useLandingData();
  const cells = useMemo(() => (signals ? sortForHeatmap(signals) : []), [signals]);

  return (
    <Section
      testId="landing-heatmap"
      title="Market heatmap"
      aside={
        <Link href="/universe/" className="text-xs text-gray-500 hover:text-gray-300">
          View full universe →
        </Link>
      }
    >
      {!loaded ? (
        <SkeletonBlock height="h-32" />
      ) : cells.length === 0 ? (
        <EmptyState>No published signals to map yet.</EmptyState>
      ) : (
        <div className="max-h-48 overflow-y-auto">
          <div className="flex flex-wrap gap-[3px]">
            {cells.map((s) => (
              <Link
                key={s.ticker}
                href={`/signal/?symbol=${s.ticker}&period=3mo`}
                title={`${s.ticker} — ${SIGNAL_LABELS[s.direction]}${
                  s.confidence != null ? ` ${Math.round(s.confidence * 100)}%` : ""
                }`}
                aria-label={`${s.ticker} ${SIGNAL_LABELS[s.direction]}`}
                className="h-3 w-3 rounded-[2px] hover:scale-150"
                style={{
                  backgroundColor: SIGNAL_COLORS[s.direction],
                  opacity: s.confidence != null ? 0.35 + 0.65 * s.confidence : 0.5,
                }}
              />
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}
