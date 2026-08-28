"use client";

/**
 * Item #16 — universe heatmap. One cell per ticker: colour = direction,
 * opacity = confidence, a corner dot for degraded / low-quality. The shape
 * of the whole basket (mostly green with three red outliers) is the
 * information; the per-ticker card layout doesn't scale past ~10 names.
 * Pure client-side render over the rows a run already fetched.
 */
import Link from "next/link";
import type { UniverseRunResult } from "@/lib/db";
import { SIGNAL_COLORS, SIGNAL_LABELS } from "@/lib/types";

interface UniverseHeatmapProps {
  results: UniverseRunResult[];
  period: string;
}

export function UniverseHeatmap({ results, period }: UniverseHeatmapProps) {
  if (results.length === 0) {
    return <p className="text-gray-600 text-sm">No results in this run.</p>;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {results.map((r) => {
        const uncovered = r.error === "uncovered";
        const failed = r.error != null && !uncovered;
        const color =
          uncovered || failed || !r.signal
            ? "#2a2a3e"
            : SIGNAL_COLORS[r.signal];
        const opacity =
          r.confidence != null ? 0.35 + 0.65 * r.confidence : 0.5;
        const lowQuality = r.dataQuality != null && r.dataQuality < 0.7;
        const title = uncovered
          ? `${r.ticker} — not in scan universe`
          : failed
            ? `${r.ticker} — ${r.error}`
            : r.signal
              ? `${r.ticker} — ${SIGNAL_LABELS[r.signal]}${
                  r.confidence != null
                    ? ` ${Math.round(r.confidence * 100)}%`
                    : ""
                }${lowQuality ? " · low data quality" : ""}${
                  r.aiDegraded ? " · AI degraded" : ""
                }`
              : `${r.ticker} — no signal`;

        return (
          <Link
            key={r.ticker}
            href={`/signal/?symbol=${r.ticker}&period=${period}`}
            title={title}
            className="relative flex h-14 w-14 items-center justify-center rounded-md text-[11px] font-semibold transition-transform hover:scale-105"
            style={{
              backgroundColor: color,
              opacity: uncovered || failed ? 0.5 : opacity,
              color:
                uncovered || failed || !r.signal ? "#8888aa" : "#0d0d1a",
              border:
                uncovered || failed
                  ? "1px dashed #555"
                  : "1px solid rgba(0,0,0,0.25)",
            }}
          >
            {r.ticker}
            {(lowQuality || r.aiDegraded) && !uncovered && !failed && (
              <span
                className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: r.aiDegraded ? "#FF6D00" : "#D50000" }}
              />
            )}
          </Link>
        );
      })}
    </div>
  );
}
