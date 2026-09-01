"use client";

/**
 * Item #17 — universe timeline / drift chart. A stacked-area SVG over a
 * universe's past runs: bullish / neutral / bearish counts over time, with
 * vertical markers where the membership `revision` changed. The trajectory
 * (basket rotating bearish over three weeks) is the signal a single run
 * can't show; the revision markers stop a membership edit being misread as
 * market movement — the same hazard diffRuns guards.
 *
 * Dependency-free inline SVG — the project ships no chart library.
 */
import type { UniverseRun } from "@/lib/db";

interface UniverseTimelineProps {
  runs: UniverseRun[];
}

const W = 640;
const H = 200;
const PAD_L = 40;
const PAD_R = 10;
const PAD_T = 10;
const PAD_B = 20;

const BEAR = "#D50000";
const NEUTRAL = "#FFD740";
const BULL = "#00C853";
const REV = "#B388FF";

export function UniverseTimeline({ runs }: UniverseTimelineProps) {
  const series = runs
    .filter((r) => r.summary != null)
    .slice()
    .sort((a, b) => a.startedAt - b.startedAt);

  if (series.length < 2) {
    return (
      <p className="text-gray-600 text-sm">
        Run the basket at least twice to see its trajectory.
      </p>
    );
  }

  const maxCounted = Math.max(
    1,
    ...series.map((r) => r.summary!.counted || 0),
  );
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const x = (i: number) =>
    PAD_L + (series.length === 1 ? 0 : (i / (series.length - 1)) * innerW);
  const y = (v: number) => PAD_T + innerH - (v / maxCounted) * innerH;

  // Cumulative band tops, bottom-up: bear, bear+neutral, bear+neutral+bull.
  const band = (pick: (s: NonNullable<UniverseRun["summary"]>) => number) =>
    series.map((r, i) => ({ i, v: pick(r.summary!) }));

  const bearTops = band((s) => s.bearish);
  const neuTops = band((s) => s.bearish + s.neutral);
  const bullTops = band((s) => s.bearish + s.neutral + s.bullish);

  const areaPath = (
    tops: { i: number; v: number }[],
    bottoms: { i: number; v: number }[] | null,
  ) => {
    const up = tops.map((p) => `${x(p.i)},${y(p.v)}`).join(" L ");
    const down = (bottoms ?? tops.map((p) => ({ i: p.i, v: 0 })))
      .slice()
      .reverse()
      .map((p) => `${x(p.i)},${y(p.v)}`)
      .join(" L ");
    return `M ${up} L ${down} Z`;
  };

  const revLines = series
    .map((r, i) =>
      i > 0 && r.universeRevision !== series[i - 1].universeRevision
        ? { i, prev: series[i - 1].universeRevision, next: r.universeRevision }
        : null,
    )
    .filter((m): m is { i: number; prev: number; next: number } => m != null);

  return (
    <div className="space-y-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        role="img"
        aria-label={`Universe direction mix over ${series.length} runs`}
      >
        {/* baseline */}
        <line
          x1={PAD_L}
          y1={PAD_T + innerH}
          x2={W - PAD_R}
          y2={PAD_T + innerH}
          stroke="#ffffff20"
        />
        <path d={areaPath(bearTops, null)} fill={BEAR} fillOpacity={0.5} />
        <path d={areaPath(neuTops, bearTops)} fill={NEUTRAL} fillOpacity={0.5} />
        <path d={areaPath(bullTops, neuTops)} fill={BULL} fillOpacity={0.5} />

        {revLines.map((m) => (
          <line
            key={m.i}
            x1={x(m.i)}
            y1={PAD_T}
            x2={x(m.i)}
            y2={PAD_T + innerH}
            stroke={REV}
            strokeDasharray="3 3"
          >
            <title>
              rev {m.prev} → {m.next}
            </title>
          </line>
        ))}
      </svg>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500">
        <span>
          <span
            className="inline-block h-2 w-2 rounded-full mr-1 align-middle"
            style={{ backgroundColor: BULL }}
          />
          bullish
        </span>
        <span>
          <span
            className="inline-block h-2 w-2 rounded-full mr-1 align-middle"
            style={{ backgroundColor: NEUTRAL }}
          />
          neutral
        </span>
        <span>
          <span
            className="inline-block h-2 w-2 rounded-full mr-1 align-middle"
            style={{ backgroundColor: BEAR }}
          />
          bearish
        </span>
        <span style={{ color: REV }}>▏ membership change</span>
      </div>
      <p className="text-[11px] text-gray-600">
        Purple lines mark membership edits — direction shifts across one are
        not market movement.
      </p>
    </div>
  );
}
