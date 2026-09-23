"use client";

/**
 * §A1 — a single horizontal strip above the heatmap/table showing the
 * bull/neutral/bear/uncovered/failed split plus run date context. Each
 * segment is a filter button (click "bear" to restrict both views to bear).
 * Pure function of `UniverseRunSummary` + the run's covered-row barTs range.
 */
import type { UniverseRunResult, UniverseRunSummary } from "@/lib/db";
import { barTsRange, summarySegments } from "@/lib/universeView";
import type { SignalDirection } from "@/lib/types";

const SEGMENT_DIRECTIONS: Record<string, SignalDirection[]> = {
  bullish: ["strong_buy", "buy"],
  bearish: ["sell", "strong_sell"],
  neutral: ["hold"],
};

interface UniverseSummaryStripProps {
  summary: UniverseRunSummary;
  results: UniverseRunResult[];
  runStartedAt: number;
  period: string;
  /** Which segment key is currently the active filter, if any. */
  activeSegment: string | null;
  onSegmentClick: (directions: SignalDirection[], key: string) => void;
}

const THREE_DAYS_MS = 3 * 24 * 60 * 60 * 1000;

export function UniverseSummaryStrip({
  summary,
  results,
  runStartedAt,
  period,
  activeSegment,
  onSegmentClick,
}: UniverseSummaryStripProps) {
  const total = Math.max(
    summary.bullish + summary.neutral + summary.bearish + summary.uncovered + summary.failed,
    1,
  );
  const segments = summarySegments(summary);
  const range = barTsRange(results);
  const wideRange = range != null && range.max - range.min > THREE_DAYS_MS;

  return (
    <div className="space-y-1.5">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-[#12121f]">
        {segments
          .filter((s) => s.count > 0)
          .map((s) => (
            <button
              key={s.key}
              type="button"
              title={`${s.label} ${s.count}`}
              onClick={() =>
                onSegmentClick(SEGMENT_DIRECTIONS[s.key] ?? [], s.key)
              }
              className="h-full transition-opacity hover:opacity-80"
              style={{
                width: `${(s.count / total) * 100}%`,
                backgroundColor: s.color,
                opacity: activeSegment && activeSegment !== s.key ? 0.35 : 1,
              }}
            />
          ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
        {segments.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() =>
              onSegmentClick(SEGMENT_DIRECTIONS[s.key] ?? [], s.key)
            }
            className={`hover:text-gray-300 ${
              activeSegment === s.key ? "text-white font-semibold" : ""
            }`}
          >
            <span style={{ color: s.color }}>■</span> {s.label} {s.count}
          </button>
        ))}
        {activeSegment && (
          <button
            type="button"
            onClick={() => onSegmentClick([], "")}
            className="text-gray-400 underline underline-offset-2 hover:text-gray-200"
          >
            clear filter
          </button>
        )}
      </div>
      <p className="text-[11px] text-gray-600">
        run {new Date(runStartedAt).toLocaleString()} · period {period}
        {range && (
          <>
            {" "}
            · bars dated{" "}
            <span className={wideRange ? "text-amber-500" : undefined}>
              {new Date(range.min).toLocaleDateString()} →{" "}
              {new Date(range.max).toLocaleDateString()}
            </span>
          </>
        )}
      </p>
    </div>
  );
}
