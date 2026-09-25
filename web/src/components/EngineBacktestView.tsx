"use client";

/**
 * Renders an engine backtest (`POST /backtest/run`): an optional hypothesis
 * verdict banner, then hit-rate bars by signal / category / strength. Every
 * bar carries its Wilson 95% band and its *own* chance marker — the hit-rate
 * a direction-blind caller with the same bullish/bearish mix would get given
 * how often these tickers rose. A bucket only shows edge when its band's
 * lower edge clears that marker.
 */
import { useState } from "react";
import type {
  EngineBacktest,
  EngineBucket,
  Focus,
  FocusGroup,
  VerdictStatus,
} from "@/lib/backtestLab";
import { THIN_BUCKET_N } from "@/lib/stats";

export const VERDICT_STYLE: Record<VerdictStatus, { label: string; color: string }> = {
  supported: { label: "Supported", color: "#00C853" },
  contradicted: { label: "Contradicted", color: "#D50000" },
  inconclusive: { label: "Inconclusive", color: "#FFD740" },
  no_data: { label: "No data", color: "#777" },
};

export function VerdictBadge({ status }: { status: VerdictStatus }) {
  const s = VERDICT_STYLE[status];
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
      style={{ color: s.color, border: `1px solid ${s.color}66`, backgroundColor: `${s.color}14` }}
    >
      {s.label}
    </span>
  );
}

function pct(x: number | null): string {
  return x == null ? "—" : `${Math.round(x * 100)}%`;
}

function BucketBar({ bucket, focused }: { bucket: EngineBucket; focused: boolean }) {
  const thin = bucket.total < THIN_BUCKET_N;
  const base = bucket.baseline;
  const color = thin
    ? "#555"
    : base != null && bucket.lower > base
      ? "#00C853"
      : base != null && bucket.upper < base
        ? "#D50000"
        : "#FFD740";
  const lo = Math.round(bucket.lower * 100);
  const hi = Math.round(bucket.upper * 100);
  const mix =
    bucket.bullish === bucket.total
      ? "all bullish"
      : bucket.bullish === 0
        ? "all bearish"
        : `${bucket.bullish} bull / ${bucket.total - bucket.bullish} bear`;

  return (
    <div
      className={`space-y-1 rounded-lg ${focused ? "bg-white/5 ring-1 ring-blue-500/50 p-2 -m-2" : ""}`}
    >
      <div className="flex justify-between gap-2 text-xs">
        <span className={`truncate ${thin ? "text-gray-600" : "text-gray-300"}`} title={bucket.key}>
          {focused && <span className="text-blue-400 mr-1">◆</span>}
          {bucket.key}
          {thin && <span className="ml-1 text-[10px] text-amber-600">n={bucket.total} — thin</span>}
        </span>
        <span className="shrink-0 text-gray-500">
          {pct(bucket.hitRate)} ({bucket.hits}/{bucket.total}) · chance {pct(base)} · {mix}
        </span>
      </div>
      <div className="relative h-3 rounded-full bg-white/5 overflow-hidden">
        <div
          className="absolute h-3 opacity-30"
          style={{ left: `${lo}%`, width: `${Math.max(1, hi - lo)}%`, backgroundColor: color }}
        />
        <div
          className="absolute h-3 w-0.5"
          style={{ left: `${Math.round(bucket.hitRate * 100)}%`, backgroundColor: color }}
        />
        {base != null && (
          <div
            className="absolute h-3 w-0.5 bg-white/60"
            style={{ left: `${Math.round(base * 100)}%` }}
            title={`chance ${pct(base)}`}
          />
        )}
      </div>
    </div>
  );
}

// Detector names produce ~90 signal buckets; show the biggest by default.
const DEFAULT_VISIBLE_BUCKETS = 12;

const GROUP_LABEL: Record<FocusGroup, string> = {
  signal: "by signal",
  category: "by category",
  strength: "by strength",
};

export function EngineBacktestView({
  result,
  focus = [],
}: {
  result: EngineBacktest;
  focus?: Focus[];
}) {
  const [group, setGroup] = useState<FocusGroup>(focus[0]?.group ?? "signal");
  const [showThin, setShowThin] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const buckets =
    group === "signal" ? result.bySignal : group === "category" ? result.byCategory : result.byStrength;
  const focusKeys = new Set(focus.filter((f) => f.group === group).map((f) => f.key));
  const visible = buckets
    .filter((b) => showThin || b.total >= THIN_BUCKET_N || focusKeys.has(b.key))
    .slice()
    .sort((a, b) => Number(focusKeys.has(b.key)) - Number(focusKeys.has(a.key)) || b.total - a.total);
  const hiddenThin = buckets.length - visible.length;
  const shown = showAll ? visible : visible.slice(0, DEFAULT_VISIBLE_BUCKETS);

  return (
    <div className="space-y-4">
      {result.verdict && (
        <div className="rounded-lg border border-white/10 bg-[#12121f] p-3 space-y-2">
          <div className="flex items-center gap-2">
            <VerdictBadge status={result.verdict.status} />
            <span className="text-xs text-gray-500">hypothesis verdict</span>
          </div>
          <ul className="space-y-1">
            {result.verdict.focuses.map((f) => (
              <li key={`${f.group}:${f.key}`} className="flex items-start gap-2 text-xs text-gray-300">
                <VerdictBadge status={f.status} />
                <span>{f.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
        <span>
          {result.symbolsOk.length} ticker{result.symbolsOk.length === 1 ? "" : "s"} replayed ·{" "}
          {result.period} daily bars · {result.horizonDays}-bar horizon
        </span>
        <span>{result.scoredBars.toLocaleString()} scored bars</span>
        {result.upRate != null && (
          <span>
            rose <span className="text-white">{pct(result.upRate)}</span> of the time (white line = chance
            for each bucket&apos;s mix)
          </span>
        )}
      </div>
      {result.symbolsFailed.length > 0 && (
        <p className="text-xs text-amber-500">
          Skipped: {result.symbolsFailed.map((f) => `${f.symbol} (${f.errorType})`).join(", ")}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-1 text-xs">
        {(["signal", "category", "strength"] as const).map((g) => (
          <button
            key={g}
            onClick={() => setGroup(g)}
            className={`rounded px-2 py-0.5 ${
              group === g ? "bg-white/15 text-white" : "text-gray-500 hover:text-gray-300"
            }`}
          >
            {GROUP_LABEL[g]}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-gray-500">
          <input type="checkbox" checked={showThin} onChange={(e) => setShowThin(e.target.checked)} />
          show thin (n&lt;{THIN_BUCKET_N})
        </label>
      </div>

      {visible.length === 0 ? (
        <p className="text-gray-600 text-sm">No buckets with enough calls at this horizon.</p>
      ) : (
        <div className="space-y-3">
          {shown.map((b) => (
            <BucketBar key={b.key} bucket={b} focused={focusKeys.has(b.key)} />
          ))}
        </div>
      )}
      {visible.length > DEFAULT_VISIBLE_BUCKETS && (
        <button
          onClick={() => setShowAll(!showAll)}
          className="text-xs text-gray-500 hover:text-gray-300 underline"
        >
          {showAll ? "show fewer" : `show all ${visible.length} buckets`}
        </button>
      )}
      {hiddenThin > 0 && !showThin && (
        <p className="text-[11px] text-gray-600">{hiddenThin} thin buckets hidden.</p>
      )}
      <p className="text-[11px] text-gray-600 leading-relaxed">
        Replays every detector on every historical daily bar and scores each directional call
        against the realized {result.horizonDays}-bar move. Bands are Wilson 95% intervals. Chance
        is not 50%: in a rising basket, always-bullish calls hit often by default — green means the
        lower bound beats chance, red means the upper bound is below it. Educational, not
        investment advice.
      </p>
    </div>
  );
}
