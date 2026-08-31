"use client";

/**
 * The /universe index: every saved basket, with its ticker count, coverage
 * badge, and last-run summary. Create a new one inline or from the current
 * watchlist. All device-local — works signed-out.
 */
import { useState } from "react";
import Link from "next/link";
import { useLiveQuery } from "dexie-react-hooks";
import { db } from "@/lib/db";
import {
  createUniverse,
  universeFromWatchlist,
  importUniverse,
  type UniverseExportDTO,
} from "@/lib/universe";

function coverageBadge(u: {
  coverage: { uncovered: string[]; inactive: string[] } | null;
}) {
  if (!u.coverage) return null;
  const { uncovered, inactive } = u.coverage;
  if (uncovered.length === 0 && inactive.length === 0) {
    return <span className="text-[11px] text-green-500">✓ fully covered</span>;
  }
  return (
    <span className="text-[11px] text-amber-500">
      {uncovered.length > 0 && `${uncovered.length} uncovered`}
      {uncovered.length > 0 && inactive.length > 0 && " · "}
      {inactive.length > 0 && `${inactive.length} inactive`}
    </span>
  );
}

export function UniverseListPanel() {
  const universes = useLiveQuery(
    async () =>
      db ? db.universes.orderBy("updatedAt").reverse().toArray() : [],
    [],
    [],
  );
  const latestRunByUniverse = useLiveQuery(
    async () => {
      if (!db) return {};
      const runs = await db.universeRuns.toArray();
      const map: Record<number, (typeof runs)[number]> = {};
      for (const run of runs) {
        const cur = map[run.universeId];
        if (!cur || run.startedAt > cur.startedAt) map[run.universeId] = run;
      }
      return map;
    },
    [],
    {} as Record<number, { summary: { bullish: number; bearish: number; neutral: number } | null; startedAt: number }>,
  );

  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleCreate() {
    setErr(null);
    if (!name.trim()) return;
    setBusy(true);
    try {
      await createUniverse(name.trim());
      setName("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleFromWatchlist() {
    setErr(null);
    setBusy(true);
    try {
      const n = name.trim() || `Watchlist ${new Date().toLocaleDateString()}`;
      await universeFromWatchlist(n);
      setName("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleImport(file: File) {
    setErr(null);
    setBusy(true);
    try {
      const dto = JSON.parse(await file.text()) as UniverseExportDTO;
      if (!dto.universe?.name) throw new Error("Not a universe export file");
      await importUniverse(dto);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "import failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            placeholder="New universe name…"
            className="flex-1 rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-green-600"
          />
          <button
            onClick={handleCreate}
            disabled={busy || !name.trim()}
            className="rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm px-4 py-2 transition-colors"
          >
            Create
          </button>
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          <button
            onClick={handleFromWatchlist}
            disabled={busy}
            className="text-gray-400 hover:text-gray-200 underline underline-offset-2"
          >
            + from watchlist
          </button>
          <label className="text-gray-400 hover:text-gray-200 underline underline-offset-2 cursor-pointer">
            + import JSON
            <input
              type="file"
              accept="application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleImport(f);
                e.target.value = "";
              }}
            />
          </label>
        </div>
        {err && <p className="text-red-400 text-xs">{err}</p>}
      </div>

      {universes && universes.length === 0 && (
        <p className="text-gray-600 text-sm">
          No universes yet. Create one above to start tracking a basket.
        </p>
      )}

      <ul className="space-y-2">
        {(universes ?? []).map((u) => {
          const run = u.id != null ? latestRunByUniverse[u.id] : undefined;
          const s = run?.summary;
          return (
            <li
              key={u.id}
              className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 hover:border-white/15 transition-colors"
            >
              <Link
                href={`/universe/?id=${u.id}`}
                className="flex items-center justify-between"
              >
                <div>
                  <span className="font-semibold text-white">{u.name}</span>
                  <span className="text-gray-500 text-sm ml-2">
                    {u.tickers.length} ticker
                    {u.tickers.length === 1 ? "" : "s"}
                  </span>
                  <div className="mt-0.5">{coverageBadge(u)}</div>
                </div>
                <div className="text-right text-xs">
                  {s ? (
                    <span>
                      <span style={{ color: "#00C853" }}>{s.bullish}▲</span>{" "}
                      <span className="text-gray-600">{s.neutral}•</span>{" "}
                      <span style={{ color: "#D50000" }}>{s.bearish}▼</span>
                    </span>
                  ) : (
                    <span className="text-gray-600">never run</span>
                  )}
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
