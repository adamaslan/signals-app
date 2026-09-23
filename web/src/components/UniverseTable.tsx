"use client";

/**
 * The per-ticker result table for a universe run. Sortable by column,
 * client-side (a universe run's results are already all in memory). Rows
 * distinguish uncovered (dashed, "not scanned") from failed (red) from a
 * real signal — the distinction the spec's §3.4 insists the UI preserve.
 *
 * At scale (§A3): a filter bar (direction / hide-uncovered / freshness /
 * min confidence / ticker search) plus windowed rendering — at most 100 rows
 * in the DOM, with "show next 100" — so a 950-name run doesn't put 950 <tr>s
 * on the page. See docs/frontend-robustness-large-universe.md.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import type { UniverseRunResult } from "@/lib/db";
import {
  SIGNAL_COLORS,
  SIGNAL_LABELS,
  SIGNAL_ARROWS,
  type SignalDirection,
} from "@/lib/types";
import { classifyFreshness, FRESHNESS_COLORS, type Freshness } from "@/lib/freshness";
import {
  DIRECTION_ORDER,
  defaultFilter,
  filterAndSortResults,
  type UniverseSortKey,
  type UniverseViewFilter,
} from "@/lib/universeView";

interface UniverseTableProps {
  results: UniverseRunResult[];
  period: string;
}

const PAGE_SIZE = 100;
const FRESHNESS_OPTIONS: Freshness[] = ["fresh", "stale", "very-stale"];

export function UniverseTable({ results, period }: UniverseTableProps) {
  const [sortKey, setSortKey] = useState<UniverseSortKey>("confidence");
  const [asc, setAsc] = useState(false);
  const [filter, setFilter] = useState<UniverseViewFilter>(() =>
    defaultFilter(results.length),
  );
  const [visible, setVisible] = useState(PAGE_SIZE);

  const filtered = useMemo(
    () => filterAndSortResults(results, filter, { sortKey, sortAsc: asc }),
    [results, filter, sortKey, asc],
  );
  const page = filtered.slice(0, visible);
  const hiddenUncoveredCount = filter.hideUncovered
    ? results.filter((r) => r.error === "uncovered").length
    : 0;

  function toggleDirection(d: SignalDirection) {
    setVisible(PAGE_SIZE);
    setFilter((f) => {
      const next = new Set(f.directions);
      if (next.has(d)) next.delete(d);
      else next.add(d);
      return { ...f, directions: next };
    });
  }

  function toggleFreshness(level: Freshness) {
    setVisible(PAGE_SIZE);
    setFilter((f) => {
      const next = new Set(f.freshness);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return { ...f, freshness: next };
    });
  }

  const header = (key: UniverseSortKey, label: string) => (
    <th
      className="py-2 pr-4 font-medium cursor-pointer select-none hover:text-gray-300"
      onClick={() => {
        if (sortKey === key) setAsc((v) => !v);
        else {
          setSortKey(key);
          setAsc(false);
        }
      }}
    >
      {label}
      {sortKey === key && <span>{asc ? " ▲" : " ▼"}</span>}
    </th>
  );

  return (
    <div className="space-y-3">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {DIRECTION_ORDER.map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => toggleDirection(d)}
            className={`rounded px-2 py-0.5 border ${
              filter.directions.has(d)
                ? "border-white/40 text-white"
                : "border-white/10 text-gray-500 hover:text-gray-300"
            }`}
            style={
              filter.directions.has(d)
                ? { backgroundColor: `${SIGNAL_COLORS[d]}22` }
                : undefined
            }
          >
            {SIGNAL_LABELS[d]}
          </button>
        ))}
        <span className="text-gray-700">|</span>
        {FRESHNESS_OPTIONS.map((level) => (
          <button
            key={level}
            type="button"
            onClick={() => toggleFreshness(level)}
            className={`rounded px-2 py-0.5 border ${
              filter.freshness.has(level)
                ? "border-white/40 text-white"
                : "border-white/10 text-gray-500 hover:text-gray-300"
            }`}
          >
            {level}
          </button>
        ))}
        <label className="flex items-center gap-1 text-gray-500">
          <input
            type="checkbox"
            checked={filter.hideUncovered}
            onChange={(e) => {
              setVisible(PAGE_SIZE);
              setFilter((f) => ({ ...f, hideUncovered: e.target.checked }));
            }}
          />
          hide uncovered
        </label>
        <label className="flex items-center gap-1 text-gray-500">
          min conf
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={filter.minConfidence}
            onChange={(e) => {
              setVisible(PAGE_SIZE);
              setFilter((f) => ({
                ...f,
                minConfidence: Number(e.target.value),
              }));
            }}
          />
          {Math.round(filter.minConfidence * 100)}%
        </label>
        <input
          value={filter.tickerSearch}
          onChange={(e) => {
            setVisible(PAGE_SIZE);
            setFilter((f) => ({ ...f, tickerSearch: e.target.value }));
          }}
          placeholder="search ticker…"
          className="rounded bg-[#12121f] border border-white/10 px-2 py-0.5 text-white placeholder-gray-600 w-28"
        />
      </div>

      <p className="text-[11px] text-gray-500">
        showing {page.length} of {filtered.length}
        {hiddenUncoveredCount > 0 &&
          ` (${hiddenUncoveredCount} uncovered hidden)`}
      </p>

      <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-[#1a1a2e] z-10">
            <tr className="text-gray-600 text-xs uppercase tracking-wider text-left">
              {header("ticker", "Ticker")}
              {header("signal", "Signal")}
              {header("confidence", "Conf.")}
              {header("confluence", "Confl.")}
              {header("dataQuality", "Data Q.")}
              {header("alignment", "Align.")}
              {header("freshness", "Fresh")}
              <th className="py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {page.map((r) => {
              const uncovered = r.error === "uncovered";
              const failed = r.error != null && !uncovered;
              const dir = r.signal;
              const color = dir ? SIGNAL_COLORS[dir] : "#666";
              const fresh = classifyFreshness(r.barTs);
              return (
                <tr key={r.ticker} className="border-t border-white/5">
                  <td className="py-2 pr-4 font-semibold text-white">
                    {r.ticker}
                  </td>
                  <td className="py-2 pr-4">
                    {uncovered ? (
                      <span
                        className="text-xs rounded px-1.5 py-0.5"
                        style={{
                          border: "1px dashed #555",
                          color: "#8888aa",
                        }}
                        title="Not in the scan universe — will always be blank"
                      >
                        not scanned
                      </span>
                    ) : failed ? (
                      <span
                        className="text-xs text-red-400"
                        title={r.error ?? undefined}
                      >
                        failed
                      </span>
                    ) : dir ? (
                      <span style={{ color }}>
                        {SIGNAL_ARROWS[dir]} {SIGNAL_LABELS[dir]}
                      </span>
                    ) : (
                      <span className="text-gray-600">—</span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-gray-400">
                    {r.confidence != null
                      ? `${Math.round(r.confidence * 100)}%`
                      : "—"}
                  </td>
                  <td className="py-2 pr-4 text-gray-400">
                    {r.confluenceScore != null
                      ? r.confluenceScore.toFixed(2)
                      : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    {r.dataQuality != null ? (
                      <span
                        style={{
                          color:
                            r.dataQuality < 0.7
                              ? "#D50000"
                              : r.dataQuality < 0.85
                                ? "#FFD740"
                                : "#00C853",
                        }}
                      >
                        {Math.round(r.dataQuality * 100)}%
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 pr-4 text-gray-400">
                    {r.alignmentScore != null
                      ? `${Math.round(r.alignmentScore * 100)}%`
                      : "—"}
                  </td>
                  <td className="py-2 pr-4">
                    {uncovered || failed ? (
                      "—"
                    ) : (
                      <span
                        style={{ color: FRESHNESS_COLORS[fresh.level] }}
                        title={
                          r.barTs != null
                            ? new Date(r.barTs).toLocaleString()
                            : undefined
                        }
                      >
                        {fresh.level === "fresh"
                          ? "fresh"
                          : fresh.ageDays != null
                            ? `${fresh.ageDays}d`
                            : "?"}
                      </span>
                    )}
                  </td>
                  <td className="py-2">
                    <Link
                      href={`/signal/?symbol=${r.ticker}&period=${period}`}
                      className="text-green-500 hover:text-green-400 text-xs"
                    >
                      open →
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {visible < filtered.length && (
        <button
          type="button"
          onClick={() => setVisible((v) => v + PAGE_SIZE)}
          className="text-xs text-green-500 hover:text-green-400 underline underline-offset-2"
        >
          Show next {Math.min(PAGE_SIZE, filtered.length - visible)}
        </button>
      )}
    </div>
  );
}
