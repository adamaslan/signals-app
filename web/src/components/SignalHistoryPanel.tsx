"use client";

/**
 * Per-ticker history log: shows every recorded analysis run for a symbol,
 * newest first, with timestamps, direction changes highlighted, and confidence.
 * Powered by Dexie useLiveQuery so the list updates immediately after each run.
 */
import { useLiveQuery } from "dexie-react-hooks";
import { db, type HistoryEntry } from "@/lib/db";
import {
  SIGNAL_COLORS,
  SIGNAL_LABELS,
  SIGNAL_ARROWS,
  getPeriodOption,
  type SignalDirection,
} from "@/lib/types";

function formatTs(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timeAgo(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** True when two consecutive entries changed direction (or one is null). */
function isDirectionChange(prev: HistoryEntry | undefined, curr: HistoryEntry): boolean {
  if (!prev) return false;
  return prev.signal !== curr.signal;
}

interface SignalHistoryPanelProps {
  ticker: string;
  limit?: number;
}

export function SignalHistoryPanel({ ticker, limit = 50 }: SignalHistoryPanelProps) {
  const runs = useLiveQuery(
    async () => {
      if (!db) return [];
      return db.history
        .where("ticker")
        .equals(ticker)
        .reverse()
        .sortBy("ts")
        .then((rows) => rows.slice(0, limit));
    },
    [ticker, limit],
    [] as HistoryEntry[],
  );

  if (!runs || runs.length === 0) {
    return (
      <p className="text-gray-600 text-sm">
        No history for {ticker} yet — run an analysis to start tracking.
      </p>
    );
  }

  // runs is newest-first; pair each entry with the one after it (older) to
  // detect direction changes as we render top-to-bottom.
  return (
    <div className="space-y-1">
      <p className="text-gray-600 text-xs mb-3">{runs.length} run{runs.length !== 1 ? "s" : ""} recorded on this device</p>
      <div className="relative">
        {/* Vertical timeline spine */}
        <div
          className="absolute left-[11px] top-0 bottom-0 w-px"
          style={{ backgroundColor: "rgba(255,255,255,0.06)" }}
          aria-hidden
        />

        <ol className="space-y-2 pl-7">
          {runs.map((run, i) => {
            const dir = run.signal as SignalDirection | null;
            const color = dir ? SIGNAL_COLORS[dir] : "#555";
            const prevRun = runs[i + 1]; // older entry (runs is newest-first)
            const changed = isDirectionChange(prevRun, run);
            const pct = run.confidence != null ? Math.round(run.confidence * 100) : null;
            const opt = getPeriodOption(run.period);

            return (
              <li key={run.id ?? i} className="relative flex items-start gap-3 group">
                {/* Timeline dot — larger + coloured on direction change */}
                <span
                  className="absolute left-[-28px] top-[6px] flex items-center justify-center rounded-full transition-transform group-hover:scale-110"
                  style={{
                    width: changed ? 14 : 10,
                    height: changed ? 14 : 10,
                    marginLeft: changed ? -2 : 0,
                    backgroundColor: color,
                    boxShadow: changed ? `0 0 8px ${color}88` : "none",
                    border: "2px solid #0d0d1a",
                  }}
                  aria-label={changed ? "Direction changed" : undefined}
                />

                <div
                  className="flex-1 rounded-lg px-3 py-2 text-sm transition-colors"
                  style={{
                    backgroundColor: changed ? `${color}12` : "rgba(255,255,255,0.03)",
                    border: changed ? `1px solid ${color}44` : "1px solid rgba(255,255,255,0.04)",
                  }}
                >
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      {dir ? (
                        <span className="font-semibold" style={{ color }}>
                          {SIGNAL_ARROWS[dir]} {SIGNAL_LABELS[dir]}
                        </span>
                      ) : (
                        <span className="text-gray-600">— no signal</span>
                      )}
                      {changed && (
                        <span
                          className="text-[10px] rounded-full px-1.5 py-0.5 font-medium uppercase tracking-wide"
                          style={{
                            backgroundColor: `${color}22`,
                            color,
                            border: `1px solid ${color}55`,
                          }}
                        >
                          direction changed
                        </span>
                      )}
                      {run.aiDegraded && (
                        <span className="text-[10px] text-orange-500 rounded-full border border-orange-800 bg-orange-950/30 px-1.5 py-0.5">
                          fallback
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-xs text-gray-500">
                      {pct != null && (
                        <span className="tabular-nums">{pct}%</span>
                      )}
                      <span>{opt?.label ?? run.period}</span>
                      {run.noLlm && <span className="text-yellow-700">no-llm</span>}
                    </div>
                  </div>

                  <div className="text-[11px] text-gray-600 mt-0.5 flex gap-2">
                    <time dateTime={new Date(run.ts).toISOString()}>
                      {formatTs(run.ts)}
                    </time>
                    <span>·</span>
                    <span>{timeAgo(run.ts)}</span>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
