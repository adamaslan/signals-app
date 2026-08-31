"use client";

/**
 * The per-ticker result table for a universe run. Sortable by column,
 * client-side (a universe run's results are already all in memory). Rows
 * distinguish uncovered (dashed, "not scanned") from failed (red) from a
 * real signal — the distinction the spec's §3.4 insists the UI preserve.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import type { UniverseRunResult } from "@/lib/db";
import {
  SIGNAL_COLORS,
  SIGNAL_LABELS,
  SIGNAL_ARROWS,
} from "@/lib/types";
import { classifyFreshness, FRESHNESS_COLORS } from "@/lib/freshness";

type SortKey =
  | "ticker"
  | "signal"
  | "confidence"
  | "confluence"
  | "dataQuality"
  | "alignment"
  | "freshness";

interface UniverseTableProps {
  results: UniverseRunResult[];
  period: string;
}

const DIR_SORT: Record<string, number> = {
  strong_sell: -2,
  sell: -1,
  hold: 0,
  buy: 1,
  strong_buy: 2,
};

export function UniverseTable({ results, period }: UniverseTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("confidence");
  const [asc, setAsc] = useState(false);

  const sorted = useMemo(() => {
    const val = (r: UniverseRunResult): number | string => {
      switch (sortKey) {
        case "ticker":
          return r.ticker;
        case "signal":
          return r.signal ? DIR_SORT[r.signal] : -99;
        case "confidence":
          return r.confidence ?? -1;
        case "confluence":
          return r.confluenceScore ?? -1;
        case "dataQuality":
          return r.dataQuality ?? -1;
        case "alignment":
          return r.alignmentScore ?? -1;
        case "freshness":
          return r.barTs ?? -1;
      }
    };
    return [...results].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      const cmp =
        typeof av === "string" && typeof bv === "string"
          ? av.localeCompare(bv)
          : (av as number) - (bv as number);
      return asc ? cmp : -cmp;
    });
  }, [results, sortKey, asc]);

  const header = (key: SortKey, label: string) => (
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
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
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
          {sorted.map((r) => {
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
                    <span style={{ color: FRESHNESS_COLORS[fresh.level] }}>
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
  );
}
