"use client";

/**
 * Item #18 + #19 — universe backtest with honest uncertainty. Hit-rate bars
 * with Wilson 95% intervals and the raw `n` on every bucket, thin buckets
 * (n < 30) greyed and flagged, and a **baseline** line (the unconditional
 * up-rate over the same bars) so a 54% hit rate in a market that rose 53%
 * of the time reads as nothing. The bucket toggle (strength / category /
 * ticker / detector) is the "which detectors carry this basket" view.
 */
import { useState } from "react";
import type { UniverseBacktest, HitRateBucketDTO } from "@/lib/db";
import { backtestUniverse } from "@/lib/universe";
import { wilsonUpperBound, THIN_BUCKET_N } from "@/lib/stats";

const HORIZONS = [5, 10, 20, 60];

type BucketView = "strength" | "category" | "ticker" | "detector";

interface UniverseBacktestPanelProps {
  universeId: number;
}

function Bar({
  bucket,
  baseline,
}: {
  bucket: HitRateBucketDTO;
  baseline: number | null;
}) {
  const thin = bucket.total < THIN_BUCKET_N;
  const pct = Math.round(bucket.hitRate * 100);
  const lower = Math.round(bucket.hitRateLower * 100);
  const upper = Math.round(wilsonUpperBound(bucket.hits, bucket.total) * 100);
  const beatsBaseline =
    baseline != null && bucket.hitRateLower > baseline;
  const color = thin
    ? "#555"
    : beatsBaseline
      ? "#00C853"
      : baseline != null && bucket.hitRate < baseline
        ? "#D50000"
        : "#FFD740";

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className={thin ? "text-gray-600" : "text-gray-300"}>
          {bucket.key}
          {thin && (
            <span className="ml-1 text-[10px] text-amber-600">
              n={bucket.total} — too thin
            </span>
          )}
        </span>
        <span className="text-gray-500">
          {pct}% ({bucket.hits}/{bucket.total})
        </span>
      </div>
      <div className="relative h-3 rounded-full bg-white/5 overflow-hidden">
        {/* Wilson interval band */}
        <div
          className="absolute h-3 opacity-30"
          style={{
            left: `${lower}%`,
            width: `${Math.max(1, upper - lower)}%`,
            backgroundColor: color,
          }}
        />
        {/* Point estimate tick */}
        <div
          className="absolute h-3 w-0.5"
          style={{ left: `${pct}%`, backgroundColor: color }}
        />
        {/* Baseline marker */}
        {baseline != null && (
          <div
            className="absolute h-3 w-0.5 bg-white/60"
            style={{ left: `${Math.round(baseline * 100)}%` }}
            title={`baseline ${Math.round(baseline * 100)}%`}
          />
        )}
      </div>
    </div>
  );
}

export function UniverseBacktestPanel({
  universeId,
}: UniverseBacktestPanelProps) {
  const [horizon, setHorizon] = useState(20);
  const [view, setView] = useState<BucketView>("strength");
  const [bt, setBt] = useState<UniverseBacktest | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run(force = false) {
    setErr(null);
    setLoading(true);
    try {
      setBt(await backtestUniverse(universeId, horizon, { force }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "backtest failed");
    } finally {
      setLoading(false);
    }
  }

  const buckets: HitRateBucketDTO[] = bt
    ? view === "strength"
      ? bt.byStrength
      : view === "ticker"
        ? bt.byTicker
        : bt.byCategory // 'category' and 'detector' both come from byCategory until a detector run
    : [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs text-gray-500">Horizon</label>
        <select
          value={horizon}
          onChange={(e) => setHorizon(Number(e.target.value))}
          className="rounded-lg bg-[#12121f] border border-white/10 px-2 py-1 text-white text-xs"
        >
          {HORIZONS.map((h) => (
            <option key={h} value={h}>
              {h}d
            </option>
          ))}
        </select>
        <button
          onClick={() => run(false)}
          disabled={loading}
          className="rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white text-xs px-3 py-1"
        >
          {loading ? "running…" : bt ? "reload" : "run backtest"}
        </button>
        {bt && (
          <button
            onClick={() => run(true)}
            disabled={loading}
            className="text-xs text-gray-500 hover:text-gray-300 underline"
          >
            recompute
          </button>
        )}
      </div>

      {err && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-3 py-2 text-red-400 text-xs">
          {err}
        </div>
      )}

      {bt && (
        <>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
            <span>
              {bt.tickersScored} of {bt.tickersRequested} tickers had data
            </span>
            <span>
              {bt.hitsTotal}/{bt.signalsTotal} directional calls hit
            </span>
            {bt.baselineUpRate != null && (
              <span>
                baseline up-rate{" "}
                <span className="text-white">
                  {Math.round(bt.baselineUpRate * 100)}%
                </span>{" "}
                (white line)
              </span>
            )}
          </div>

          <div className="flex gap-1 text-xs">
            {(["strength", "category", "ticker"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`rounded px-2 py-0.5 ${
                  view === v
                    ? "bg-white/15 text-white"
                    : "text-gray-500 hover:text-gray-300"
                }`}
              >
                by {v}
              </button>
            ))}
          </div>

          {buckets.length === 0 ? (
            <p className="text-gray-600 text-sm">
              No {view} buckets with data at this horizon. `forward_returns`
              may be sparse for these tickers.
            </p>
          ) : (
            <div className="space-y-2.5">
              {buckets
                .slice()
                .sort((a, b) => b.total - a.total)
                .map((bucket) => (
                  <Bar
                    key={bucket.key}
                    bucket={bucket}
                    baseline={bt.baselineUpRate}
                  />
                ))}
            </div>
          )}

          <p className="text-[11px] text-gray-600 leading-relaxed">
            Bands are Wilson 95% confidence intervals; the tick is the point
            estimate. A bucket is only meaningfully better than chance when
            its <em>lower</em> bound clears the baseline. Buckets with n &lt;{" "}
            {THIN_BUCKET_N} are greyed — a 100%-hit-rate bucket with n=3 is
            noise, not signal.
          </p>
        </>
      )}
    </div>
  );
}
