"use client";

/** Star toggle that adds/removes a ticker from the local watchlist. */
import { useLiveQuery } from "dexie-react-hooks";
import { db, addToWatchlist, removeFromWatchlist } from "@/lib/db";
import { useAuth } from "@/lib/auth";
import { syncWatchlistAdd, syncWatchlistRemove } from "@/lib/sync";

export function WatchlistButton({ ticker }: { ticker: string }) {
  const { user } = useAuth();
  const watched = useLiveQuery(
    async () => (db ? (await db.watchlist.get(ticker)) != null : false),
    [ticker],
    false,
  );

  async function toggle() {
    if (watched) {
      await removeFromWatchlist(ticker);
      if (user) await syncWatchlistRemove(user.id, ticker);
    } else {
      await addToWatchlist(ticker);
      if (user) await syncWatchlistAdd(user.id, ticker);
    }
  }

  return (
    <button
      onClick={toggle}
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
