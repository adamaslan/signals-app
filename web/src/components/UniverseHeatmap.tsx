"use client";

/**
 * Item #16 — universe heatmap. One cell per ticker: colour = direction,
 * opacity = confidence, a corner dot for degraded / low-quality. At scale
 * (§A2) it groups by direction, collapses uncovered/failed into one line,
 * and switches tile density so a 950-name basket still reads as a shape
 * instead of a grey wall — see docs/frontend-robustness-large-universe.md.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { UniverseRunResult } from "@/lib/db";
import { SIGNAL_COLORS, SIGNAL_LABELS } from "@/lib/types";
import {
  groupForHeatmap,
  heatmapDensity,
  type HeatmapDensity,
} from "@/lib/universeView";

interface UniverseHeatmapProps {
  results: UniverseRunResult[];
  period: string;
}

const TILE_SIZE: Record<HeatmapDensity, string> = {
  labelled: "h-14 w-14 text-[11px]",
  compact: "h-7 w-7 text-[9px]",
  dense: "h-3 w-3 text-[0px]",
};

const OPACITY_FLOOR: Record<HeatmapDensity, number> = {
  labelled: 0.35,
  compact: 0.35,
  dense: 0.5,
};

function tileTitle(r: UniverseRunResult): string {
  const uncovered = r.error === "uncovered";
  const failed = r.error != null && !uncovered;
  const lowQuality = r.dataQuality != null && r.dataQuality < 0.7;
  if (uncovered) return `${r.ticker} — not in scan universe`;
  if (failed) return `${r.ticker} — ${r.error}`;
  if (!r.signal) return `${r.ticker} — no signal`;
  return `${r.ticker} — ${SIGNAL_LABELS[r.signal]}${
    r.confidence != null ? ` ${Math.round(r.confidence * 100)}%` : ""
  }${lowQuality ? " · low data quality" : ""}${
    r.aiDegraded ? " · AI degraded" : ""
  }`;
}

export function UniverseHeatmap({ results, period }: UniverseHeatmapProps) {
  const router = useRouter();
  const [showCollapsed, setShowCollapsed] = useState(false);

  if (results.length === 0) {
    return <p className="text-gray-600 text-sm">No results in this run.</p>;
  }

  const { groups, noSignal, uncovered, failed } = groupForHeatmap(results);
  const coveredCount = groups.reduce((n, g) => n + g.results.length, 0) + noSignal.length;
  const density = heatmapDensity(coveredCount);
  const floor = OPACITY_FLOOR[density];

  // One delegated click handler on the grid container instead of one
  // Next.js <Link> per tile — at 950 tiles, 950 individual <Link>s each
  // register their own viewport prefetch observer, which is the actual
  // cost in `next dev` (a burst of ~950 route fetches on scroll/paint).
  function handleGridClick(e: React.MouseEvent<HTMLDivElement>) {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-ticker]");
    const ticker = el?.dataset.ticker;
    if (ticker) router.push(`/signal/?symbol=${ticker}&period=${period}`);
  }

  function renderTile(r: UniverseRunResult, dim = false) {
    const color = dim || !r.signal ? "#2a2a3e" : SIGNAL_COLORS[r.signal];
    const opacity = dim
      ? 0.5
      : r.confidence != null
        ? floor + (1 - floor) * r.confidence
        : 0.5;
    const lowQuality = r.dataQuality != null && r.dataQuality < 0.7;
    return (
      <button
        key={r.ticker}
        type="button"
        data-ticker={r.ticker}
        title={tileTitle(r)}
        className={`relative flex items-center justify-center rounded-md font-semibold transition-transform hover:scale-105 ${TILE_SIZE[density]}`}
        style={{
          backgroundColor: color,
          opacity,
          color: dim || !r.signal ? "#8888aa" : "#0d0d1a",
          border: dim ? "1px dashed #555" : "1px solid rgba(0,0,0,0.25)",
        }}
      >
        {density !== "dense" && r.ticker}
        {(lowQuality || r.aiDegraded) && !dim && (
          <span
            className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: r.aiDegraded ? "#FF6D00" : "#D50000" }}
          />
        )}
      </button>
    );
  }

  return (
    <div className="space-y-3" onClick={handleGridClick}>
      {groups.map((g) => (
        <div key={g.direction} className="space-y-1">
          <p className="text-[10px] uppercase tracking-widest text-gray-600">
            {SIGNAL_LABELS[g.direction]} · {g.results.length}
          </p>
          <div className="flex flex-wrap gap-1">
            {g.results.map((r) => renderTile(r))}
          </div>
        </div>
      ))}
      {noSignal.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-widest text-gray-600">
            No signal · {noSignal.length}
          </p>
          <div className="flex flex-wrap gap-1">
            {noSignal.map((r) => renderTile(r))}
          </div>
        </div>
      )}
      {(uncovered.length > 0 || failed.length > 0) && (
        <div className="text-xs text-gray-500">
          {!showCollapsed ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowCollapsed(true);
              }}
              className="underline underline-offset-2 hover:text-gray-300"
            >
              {uncovered.length > 0 && `${uncovered.length} not scanned`}
              {uncovered.length > 0 && failed.length > 0 && " · "}
              {failed.length > 0 && `${failed.length} failed`}
              {" [show]"}
            </button>
          ) : (
            <div className="space-y-1">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setShowCollapsed(false);
                }}
                className="underline underline-offset-2 hover:text-gray-300"
              >
                [hide]
              </button>
              <div className="flex flex-wrap gap-1">
                {[...uncovered, ...failed].map((r) => renderTile(r, true))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
