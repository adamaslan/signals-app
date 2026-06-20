"use client";

/**
 * Live table of recent analysis runs from the local DB. Each row links back to
 * re-run the same ticker + period. Powered by Dexie's useLiveQuery so it
 * updates instantly when a new run is recorded.
 */
import Link from "next/link";
import { useLiveQuery } from "dexie-react-hooks";
import { db } from "@/lib/db";
import {
  SIGNAL_COLORS,
  SIGNAL_LABELS,
  SIGNAL_ARROWS,
  getPeriodOption,
  resolveBackendPeriod,
  type SignalDirection,
} from "@/lib/types";

function timeAgo(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function RecentRunsTable({ limit = 15 }: { limit?: number }) {
  const runs = useLiveQuery(
    async () =>
      db ? db.history.orderBy("ts").reverse().limit(limit).toArray() : [],
    [limit],
    [],
  );

  if (!runs || runs.length === 0) {
    return (
      <p className="text-gray-600 text-sm">
        No runs yet — analyse a ticker to start your history.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-600 text-xs uppercase tracking-wider text-left">
            <th className="py-2 pr-4 font-medium">Ticker</th>
            <th className="py-2 pr-4 font-medium">Signal</th>
            <th className="py-2 pr-4 font-medium">Period</th>
            <th className="py-2 pr-4 font-medium">Conf.</th>
            <th className="py-2 pr-4 font-medium">When</th>
            <th className="py-2 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const dir = r.signal as SignalDirection | null;
            const color = dir ? SIGNAL_COLORS[dir] : "#666";
            const opt = getPeriodOption(r.period);
            const href = `/signals/${r.ticker}?period=${r.period}&no_llm=${r.noLlm}`;
            return (
              <tr key={r.id} className="border-t border-white/5">
                <td className="py-2 pr-4 font-semibold text-white">{r.ticker}</td>
                <td className="py-2 pr-4">
                  {dir ? (
                    <span style={{ color }}>
                      {SIGNAL_ARROWS[dir]} {SIGNAL_LABELS[dir]}
                    </span>
                  ) : (
                    <span className="text-gray-600">—</span>
                  )}
                </td>
                <td className="py-2 pr-4 text-gray-400">
                  {opt?.label ?? r.period}
                  {opt && !opt.supported && (
                    <span className="text-amber-600 text-[10px]">
                      {" "}
                      →{resolveBackendPeriod(r.period)}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4 text-gray-400">
                  {r.confidence != null ? `${Math.round(r.confidence * 100)}%` : "—"}
                </td>
                <td className="py-2 pr-4 text-gray-600">{timeAgo(r.ts)}</td>
                <td className="py-2">
                  <Link
                    href={href}
                    className="text-green-500 hover:text-green-400 text-xs"
                  >
                    re-run →
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
