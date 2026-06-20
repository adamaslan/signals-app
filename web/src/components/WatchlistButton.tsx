"use client";

/** Star toggle that adds/removes a ticker from the local watchlist. */
import { useLiveQuery } from "dexie-react-hooks";
import { db, addToWatchlist, removeFromWatchlist } from "@/lib/db";

export function WatchlistButton({ ticker }: { ticker: string }) {
  const watched = useLiveQuery(
    async () => (db ? (await db.watchlist.get(ticker)) != null : false),
    [ticker],
    false,
  );

  return (
    <button
      onClick={() => (watched ? removeFromWatchlist(ticker) : addToWatchlist(ticker))}
      className={[
        "rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
        watched
          ? "bg-amber-500/20 border-amber-500 text-amber-300"
          : "bg-[#1a1a2e] border-white/10 text-gray-400 hover:border-white/30",
      ].join(" ")}
      aria-pressed={watched}
      title={watched ? "Remove from watchlist" : "Add to watchlist"}
    >
      {watched ? "★ Watching" : "☆ Watch"}
    </button>
  );
}
