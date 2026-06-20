"use client";

/** Live watchlist sidebar — starred tickers with their last seen signal. */
import Link from "next/link";
import { useLiveQuery } from "dexie-react-hooks";
import { db, removeFromWatchlist } from "@/lib/db";
import {
  SIGNAL_COLORS,
  SIGNAL_ARROWS,
  type SignalDirection,
} from "@/lib/types";

export function WatchlistPanel() {
  const items = useLiveQuery(
    async () => (db ? db.watchlist.orderBy("addedAt").reverse().toArray() : []),
    [],
    [],
  );

  if (!items || items.length === 0) {
    return (
      <p className="text-gray-600 text-sm">
        Star a ticker (☆) on its page to track it here.
      </p>
    );
  }

  return (
    <ul className="space-y-1.5">
      {items.map((w) => {
        const dir = w.lastSignal as SignalDirection | null;
        const color = dir ? SIGNAL_COLORS[dir] : "#666";
        return (
          <li
            key={w.ticker}
            className="flex items-center justify-between rounded-lg bg-[#12121f] border border-white/5 px-3 py-2"
          >
            <Link
              href={`/signals/${w.ticker}`}
              className="font-semibold text-white hover:text-green-400"
            >
              {w.ticker}
            </Link>
            <div className="flex items-center gap-3">
              <span style={{ color }} className="text-sm">
                {dir ? SIGNAL_ARROWS[dir] : "·"}
              </span>
              <button
                onClick={() => removeFromWatchlist(w.ticker)}
                className="text-gray-600 hover:text-red-400 text-xs"
                title="Remove"
              >
                ✕
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
