"use client";

/**
 * Admin profile — backtest history. There's no separate account system in
 * this app: the single on-device profile (db.ts PROFILE_ID) *is* the admin,
 * and this lists every cached backtest it has ever run, across every local
 * universe, newest first.
 */
import Link from "next/link";
import { useLiveQuery } from "dexie-react-hooks";
import { getBacktestHistory } from "@/lib/universe";
import { useProfile } from "@/lib/useProfile";

function fmtDate(ts: number): string {
  return new Date(ts).toLocaleString();
}

export function AdminBacktestHistory() {
  const { profile, ready } = useProfile();
  const history = useLiveQuery(() => getBacktestHistory(), [], null);

  return (
    <div className="space-y-6">
      <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4">
        <div className="text-xs text-gray-500 uppercase tracking-wide">
          Admin
        </div>
        <div className="text-white font-semibold mt-1">
          {ready ? profile?.name ?? "Trader" : "…"}
        </div>
        <div className="text-gray-500 text-xs mt-1">
          Total analyses run: {ready ? profile?.totalRuns ?? 0 : "…"} · backtests
          cached: {history?.length ?? "…"}
        </div>
      </div>

      {history != null && history.length === 0 && (
        <p className="text-gray-600 text-sm">
          No backtests yet. Run one from a universe page — its result will
          show up here.
        </p>
      )}

      <ul className="space-y-2">
        {(history ?? []).map((bt) => (
          <li
            key={bt.id}
            className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4"
          >
            <Link
              href={`/universe/?id=${bt.universeId}`}
              className="flex items-center justify-between gap-4"
            >
              <div>
                <span className="font-semibold text-white">
                  {bt.universeName}
                </span>
                <span className="text-gray-500 text-sm ml-2">
                  {bt.horizonDays}d horizon
                </span>
                <div className="text-gray-600 text-xs mt-0.5">
                  {fmtDate(bt.ranAt)}
                </div>
              </div>
              <div className="text-right text-xs text-gray-400">
                <div>
                  {bt.hitsTotal}/{bt.signalsTotal} hits
                </div>
                <div className="text-gray-600">
                  {bt.tickersScored}/{bt.tickersRequested} tickers scored
                </div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
