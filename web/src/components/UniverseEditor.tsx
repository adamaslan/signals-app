"use client";

/**
 * The single-universe workspace: edit membership (with a paste box that
 * never silently drops), run a coverage check, run the whole basket in one
 * batched read, and view the newest run as a table or heatmap plus a drift
 * comparison against the previous run.
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useLiveQuery } from "dexie-react-hooks";
import { db, type Universe, type UniverseRun } from "@/lib/db";
import {
  getUniverse,
  renameUniverse,
  updateUniverseMeta,
  deleteUniverse,
  addTicker,
  removeTicker,
  importTickersFromText,
  refreshCoverage,
  runUniverse,
  exportUniverse,
  exportUniverseCsv,
  watchlistFromUniverse,
} from "@/lib/universe";
import { VALID_PERIODS } from "@/lib/types";
import { requestCoverage, fetchMyCoverageRequests } from "@/lib/api";
import { UniverseTable } from "./UniverseTable";
import { UniverseHeatmap } from "./UniverseHeatmap";
import { UniverseDriftView } from "./UniverseDriftView";
import { UniverseBacktestPanel } from "./UniverseBacktestPanel";
import { UniverseTimeline } from "./UniverseTimeline";

function download(name: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

interface UniverseEditorProps {
  universeId: number;
}

export function UniverseEditor({ universeId }: UniverseEditorProps) {
  const universe = useLiveQuery(
    async () => (db ? ((await db.universes.get(universeId)) ?? null) : null),
    [universeId],
    undefined,
  );
  const runs = useLiveQuery(
    async () =>
      db
        ? db.universeRuns
            .where("universeId")
            .equals(universeId)
            .reverse()
            .sortBy("startedAt")
        : [],
    [universeId],
    [] as UniverseRun[],
  );

  const [paste, setPaste] = useState("");
  const [pasteResult, setPasteResult] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [checkingCoverage, setCheckingCoverage] = useState(false);
  const [view, setView] = useState<"table" | "heatmap">("heatmap");
  const [err, setErr] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [requested, setRequested] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (universe) setNameDraft(universe.name);
  }, [universe?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let active = true;
    fetchMyCoverageRequests()
      .then((m) => active && setRequested((prev) => new Set([...prev, ...m.keys()])))
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  async function handleRequestCoverage(ticker: string) {
    try {
      await requestCoverage(ticker);
      setRequested((s) => new Set(s).add(ticker));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "request failed");
    }
  }

  const latestRun = runs[0];
  const prevRun = runs[1];

  const period = universe?.defaultPeriod ?? "3mo";

  async function handlePasteImport() {
    setErr(null);
    setPasteResult(null);
    try {
      const res = await importTickersFromText(universeId, paste);
      setPaste("");
      const parts: string[] = [];
      if (res.added.length) parts.push(`added ${res.added.length}`);
      if (res.skipped.length)
        parts.push(`already present ${res.skipped.length}`);
      if (res.invalid.length)
        parts.push(
          `couldn't parse ${res.invalid.length}: ${res.invalid
            .slice(0, 5)
            .join(", ")}`,
        );
      setPasteResult(parts.join(" · ") || "nothing to add");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "import failed");
    }
  }

  async function handleRun() {
    setErr(null);
    setRunning(true);
    try {
      await runUniverse(universeId, { period });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "run failed");
    } finally {
      setRunning(false);
    }
  }

  async function handleCoverage() {
    setErr(null);
    setCheckingCoverage(true);
    try {
      await refreshCoverage(universeId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "coverage check failed");
    } finally {
      setCheckingCoverage(false);
    }
  }

  async function handleDelete() {
    if (!universe) return;
    if (
      !confirm(
        `Delete "${universe.name}" and its ${runs.length} run(s)? This can't be undone.`,
      )
    )
      return;
    await deleteUniverse(universeId);
    window.location.href = "/universe/";
  }

  if (universe === undefined) {
    return <p className="text-gray-600 text-sm">Loading…</p>;
  }
  if (universe === null) {
    return (
      <div className="rounded-xl border border-white/10 bg-[#1a1a2e] p-6 text-center space-y-2">
        <p className="text-gray-300 font-semibold">Universe not found</p>
        <Link href="/universe/" className="text-green-500 text-sm">
          ← All universes
        </Link>
      </div>
    );
  }

  const cov = universe.coverage;

  return (
    <div className="space-y-6">
      {/* Header: name + actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <input
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={() => {
              if (nameDraft.trim() && nameDraft !== universe.name) {
                renameUniverse(universeId, nameDraft.trim()).catch((e) =>
                  setErr(e instanceof Error ? e.message : "rename failed"),
                );
              }
            }}
            className="bg-transparent text-2xl font-extrabold text-white border-b border-transparent hover:border-white/20 focus:border-green-600 focus:outline-none"
          />
          <span className="text-gray-600 text-xs">rev {universe.revision}</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <button
            onClick={() => watchlistFromUniverse(universeId)}
            className="text-gray-400 hover:text-gray-200 underline underline-offset-2"
          >
            → watchlist
          </button>
          <button
            onClick={async () =>
              download(
                `${universe.name}.json`,
                JSON.stringify(await exportUniverse(universeId), null, 2),
                "application/json",
              )
            }
            className="text-gray-400 hover:text-gray-200 underline underline-offset-2"
          >
            export JSON
          </button>
          <button
            onClick={async () =>
              download(
                `${universe.name}.csv`,
                await exportUniverseCsv(universeId),
                "text/csv",
              )
            }
            className="text-gray-400 hover:text-gray-200 underline underline-offset-2"
          >
            export CSV
          </button>
          <button
            onClick={handleDelete}
            className="text-red-500 hover:text-red-400 underline underline-offset-2"
          >
            delete
          </button>
        </div>
      </div>

      {err && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-3 py-2 text-red-400 text-sm">
          {err}
        </div>
      )}

      {/* Note + period */}
      <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
        <textarea
          defaultValue={universe.note}
          onBlur={(e) =>
            updateUniverseMeta(universeId, { note: e.target.value })
          }
          placeholder="Thesis for this basket…"
          rows={2}
          className="w-full rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-green-600 resize-y"
        />
        <div className="flex items-center gap-2 text-sm">
          <label className="text-gray-500">Default period</label>
          <select
            value={universe.defaultPeriod}
            onChange={(e) =>
              updateUniverseMeta(universeId, { defaultPeriod: e.target.value })
            }
            className="rounded-lg bg-[#12121f] border border-white/10 px-2 py-1 text-white"
          >
            {VALID_PERIODS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Membership */}
      <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Tickers ({universe.tickers.length})
          </h2>
          <button
            onClick={handleCoverage}
            disabled={checkingCoverage || universe.tickers.length === 0}
            className="text-xs text-gray-400 hover:text-gray-200 underline underline-offset-2 disabled:opacity-40"
          >
            {checkingCoverage ? "checking…" : "check coverage"}
          </button>
        </div>

        {cov && (
          <div className="text-xs space-y-0.5">
            <p className="text-green-500">
              ✅ {cov.covered.length} of {universe.tickers.length} in the scan
              universe
            </p>
            {cov.inactive.length > 0 && (
              <p className="text-amber-500">
                ⚠️ {cov.inactive.join(", ")} — known but inactive, signals may
                be stale
              </p>
            )}
            {cov.uncovered.length > 0 && (
              <div className="text-red-400 space-y-1">
                <p>
                  ❌ not in the scan universe, will always be blank:
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {cov.uncovered.map((t) => (
                    <span key={t} className="inline-flex items-center gap-1">
                      <span className="font-semibold">{t}</span>
                      {requested.has(t) ? (
                        <span className="text-gray-500 text-[10px]">
                          requested ✓
                        </span>
                      ) : (
                        <button
                          onClick={() => handleRequestCoverage(t)}
                          className="text-[10px] text-blue-400 hover:text-blue-300 underline"
                        >
                          request coverage
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-1.5">
          {universe.tickers.map((t) => {
            const uncovered = cov?.uncovered.includes(t);
            const inactive = cov?.inactive.includes(t);
            return (
              <span
                key={t}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs"
                style={{
                  backgroundColor: "#12121f",
                  border: `1px solid ${
                    uncovered ? "#D5000066" : inactive ? "#FFD74066" : "#ffffff14"
                  }`,
                  color: uncovered ? "#ff8a8a" : "#fff",
                }}
              >
                {t}
                <button
                  onClick={() => removeTicker(universeId, t)}
                  className="text-gray-600 hover:text-red-400"
                  title="Remove"
                >
                  ✕
                </button>
              </span>
            );
          })}
        </div>

        <div className="space-y-2">
          <textarea
            value={paste}
            onChange={(e) => setPaste(e.target.value)}
            placeholder="Paste tickers — commas, spaces, newlines, a spreadsheet column, $-prefixed, all fine"
            rows={2}
            className="w-full rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-green-600 resize-y"
          />
          <button
            onClick={handlePasteImport}
            disabled={!paste.trim()}
            className="rounded-lg bg-white/10 hover:bg-white/20 disabled:opacity-40 text-white text-sm px-3 py-1.5"
          >
            Add pasted
          </button>
          {pasteResult && (
            <p className="text-xs text-gray-400">{pasteResult}</p>
          )}
        </div>
      </div>

      {/* Run */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleRun}
          disabled={running || universe.tickers.length === 0}
          className="rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-sm px-4 py-2 transition-colors"
        >
          {running ? "Running…" : `Run basket (${period})`}
        </button>
        {latestRun && (
          <span className="text-xs text-gray-500">
            last run {new Date(latestRun.startedAt).toLocaleString()} ·{" "}
            {latestRun.status}
          </span>
        )}
      </div>

      {/* Latest run */}
      {latestRun && (
        <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
              Latest run
            </h2>
            <div className="flex gap-1 text-xs">
              {(["heatmap", "table"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`rounded px-2 py-0.5 ${
                    view === v
                      ? "bg-white/15 text-white"
                      : "text-gray-500 hover:text-gray-300"
                  }`}
                >
                  {v}
                </button>
              ))}
            </div>
          </div>

          {latestRun.summary && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
              <span>
                <span style={{ color: "#00C853" }}>
                  {latestRun.summary.bullish}
                </span>{" "}
                bull ·{" "}
                <span className="text-gray-600">
                  {latestRun.summary.neutral}
                </span>{" "}
                hold ·{" "}
                <span style={{ color: "#D50000" }}>
                  {latestRun.summary.bearish}
                </span>{" "}
                bear
              </span>
              <span>{latestRun.summary.uncovered} uncovered</span>
              <span>{latestRun.summary.failed} failed</span>
              {latestRun.summary.avgConfidence != null && (
                <span>
                  avg conf{" "}
                  {Math.round(latestRun.summary.avgConfidence * 100)}%
                </span>
              )}
              {latestRun.summary.avgAlignment != null && (
                <span>
                  avg align{" "}
                  {Math.round(latestRun.summary.avgAlignment * 100)}%
                </span>
              )}
            </div>
          )}

          {view === "heatmap" ? (
            <UniverseHeatmap
              results={latestRun.results}
              period={latestRun.period}
            />
          ) : (
            <UniverseTable
              results={latestRun.results}
              period={latestRun.period}
            />
          )}
        </div>
      )}

      {/* Drift vs previous run */}
      {latestRun && prevRun && latestRun.id != null && prevRun.id != null && (
        <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Changed since previous run
          </h2>
          <UniverseDriftView
            prevRunId={prevRun.id}
            nextRunId={latestRun.id}
          />
        </div>
      )}

      {/* Timeline */}
      {runs.length > 1 && (
        <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Trajectory
          </h2>
          <UniverseTimeline runs={runs} />
        </div>
      )}

      {/* Backtest */}
      {universe.tickers.length > 0 && (
        <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Backtest
          </h2>
          <UniverseBacktestPanel universeId={universeId} />
        </div>
      )}

      {/* Run history */}
      {runs.length > 1 && (
        <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-2">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Run history
          </h2>
          <ul className="text-xs text-gray-500 space-y-1">
            {runs.map((r) => (
              <li key={r.id} className="flex justify-between">
                <span>{new Date(r.startedAt).toLocaleString()}</span>
                <span>
                  {r.status} · rev {r.universeRevision} ·{" "}
                  {r.summary
                    ? `${r.summary.bullish}▲ ${r.summary.bearish}▼`
                    : "—"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
