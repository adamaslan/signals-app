"use client";

/**
 * Rich period control panel — "lots of ways to manipulate the signal config".
 *
 * Three coordinated controls over the same value:
 *   1. A range slider spanning 1 minute → lifetime (MAX).
 *   2. Grouped preset chips (Intraday / Swing / Long), keyboard-navigable.
 *   3. A "rule-based only" toggle.
 *
 * Periods the backend can't serve yet are shown with a "beta" tag and are
 * transparently mapped to the nearest supported period (see resolveBackendPeriod).
 */
import { useMemo } from "react";
import {
  PERIOD_OPTIONS,
  getPeriodOption,
  resolveBackendPeriod,
  type PeriodOption,
} from "@/lib/types";

interface Props {
  /** Current UI period id. */
  period: string;
  onPeriodChange: (id: string) => void;
  noLlm: boolean;
  onNoLlmChange: (v: boolean) => void;
}

const GROUP_LABELS: Record<PeriodOption["group"], string> = {
  intraday: "Intraday",
  swing: "Swing",
  long: "Long-term",
};

export function PeriodControlPanel({
  period,
  onPeriodChange,
  noLlm,
  onNoLlmChange,
}: Props) {
  const index = useMemo(
    () => Math.max(0, PERIOD_OPTIONS.findIndex((o) => o.id === period)),
    [period],
  );
  const current = getPeriodOption(period) ?? PERIOD_OPTIONS[7];
  const backendPeriod = resolveBackendPeriod(period);
  const mapped = !current.supported;

  const grouped = useMemo(() => {
    const groups: Record<string, PeriodOption[]> = {
      intraday: [],
      swing: [],
      long: [],
    };
    for (const o of PERIOD_OPTIONS) groups[o.group].push(o);
    return groups;
  }, []);

  return (
    <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
          Time Period
        </h2>
        <div className="text-right">
          <span className="text-white text-lg font-bold">{current.longLabel}</span>
          {mapped && (
            <span
              className="ml-2 align-middle rounded-full bg-amber-900/40 border border-amber-600/50 text-amber-400 text-[10px] px-2 py-0.5"
              title={`Not yet served by the backend — analysing nearest supported range (${backendPeriod}).`}
            >
              beta → {backendPeriod}
            </span>
          )}
        </div>
      </div>

      {/* Slider across the full range */}
      <div className="space-y-2">
        <input
          type="range"
          min={0}
          max={PERIOD_OPTIONS.length - 1}
          step={1}
          value={index}
          onChange={(e) => onPeriodChange(PERIOD_OPTIONS[Number(e.target.value)].id)}
          className="w-full accent-green-500"
          aria-label="Time period slider"
          list="period-ticks"
        />
        <datalist id="period-ticks">
          {PERIOD_OPTIONS.map((o, i) => (
            <option key={o.id} value={i} label={o.label} />
          ))}
        </datalist>
        <div className="flex justify-between text-[10px] text-gray-600">
          <span>1m</span>
          <span>1D</span>
          <span>1Y</span>
          <span>Lifetime</span>
        </div>
      </div>

      {/* Grouped preset chips */}
      <div className="space-y-3">
        {(["intraday", "swing", "long"] as const).map((g) => (
          <div key={g}>
            <p className="text-[10px] uppercase tracking-widest text-gray-600 mb-1.5">
              {GROUP_LABELS[g]}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {grouped[g].map((o) => {
                const active = o.id === period;
                return (
                  <button
                    key={o.id}
                    onClick={() => onPeriodChange(o.id)}
                    title={
                      o.supported
                        ? o.longLabel
                        : `${o.longLabel} — maps to ${o.supportedFallback}`
                    }
                    className={[
                      "rounded-lg px-2.5 py-1 text-xs font-medium transition-colors border",
                      active
                        ? "bg-green-700 border-green-500 text-white"
                        : "bg-[#12121f] border-white/10 text-gray-300 hover:border-white/30",
                      !o.supported && !active ? "opacity-70" : "",
                    ].join(" ")}
                  >
                    {o.label}
                    {!o.supported && (
                      <span className="ml-1 text-[8px] text-amber-500">β</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Rule-based only toggle */}
      <label className="flex items-center gap-3 cursor-pointer select-none pt-1 border-t border-white/5">
        <input
          type="checkbox"
          checked={noLlm}
          onChange={(e) => onNoLlmChange(e.target.checked)}
          className="w-4 h-4 rounded border-white/20 bg-[#12121f] accent-green-500"
        />
        <span className="text-sm text-gray-400">
          Rule-based only{" "}
          <span className="text-gray-600">(skip AI synthesis — faster)</span>
        </span>
      </label>
    </div>
  );
}
