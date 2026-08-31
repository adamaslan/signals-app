"use client";

/**
 * The "what changed between two runs" view (§3.3). Refuses to *silently*
 * compare across differing universe revisions — it shows a warning banner so
 * a membership edit can't masquerade as market movement.
 */
import { useEffect, useState } from "react";
import {
  diffRuns,
  type UniverseDriftDTO,
  type DriftClass,
} from "@/lib/universe";
import {
  SIGNAL_LABELS,
  SIGNAL_COLORS,
  type SignalDirection,
} from "@/lib/types";

interface UniverseDriftViewProps {
  prevRunId: number;
  nextRunId: number;
}

const CLASS_LABEL: Record<DriftClass, string> = {
  upgraded: "Upgraded",
  downgraded: "Downgraded",
  unchanged: "Unchanged",
  new: "New",
  dropped: "Dropped",
  "newly-covered": "Newly covered",
  "went-stale": "Went stale",
};

const CLASS_COLOR: Record<DriftClass, string> = {
  upgraded: "#00C853",
  downgraded: "#D50000",
  unchanged: "#666",
  new: "#69F0AE",
  dropped: "#FF6D00",
  "newly-covered": "#40C4FF",
  "went-stale": "#FFD740",
};

function sig(d: SignalDirection | null) {
  if (!d) return <span className="text-gray-600">—</span>;
  return <span style={{ color: SIGNAL_COLORS[d] }}>{SIGNAL_LABELS[d]}</span>;
}

export function UniverseDriftView({
  prevRunId,
  nextRunId,
}: UniverseDriftViewProps) {
  const [drift, setDrift] = useState<UniverseDriftDTO | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    diffRuns(prevRunId, nextRunId)
      .then((d) => active && setDrift(d))
      .catch((e) => active && setError(e instanceof Error ? e.message : "failed"));
    return () => {
      active = false;
    };
  }, [prevRunId, nextRunId]);

  if (error) return <p className="text-red-400 text-sm">{error}</p>;
  if (!drift) return <p className="text-gray-600 text-sm">Comparing…</p>;

  const moved = drift.entries.filter(
    (e) => e.class !== "unchanged" && e.class !== "dropped",
  );

  return (
    <div className="space-y-3">
      {drift.revisionMismatch && (
        <div className="rounded-lg border border-amber-700 bg-amber-950/30 px-3 py-2 text-amber-400 text-xs">
          ⚠ These two runs used different basket memberships (revision{" "}
          {drift.prevRevision} → {drift.nextRevision}). Some changes below are
          membership edits, not market movement.
        </div>
      )}
      {moved.length === 0 ? (
        <p className="text-gray-500 text-sm">
          No direction changes between these runs.
        </p>
      ) : (
        <ul className="space-y-1 text-sm">
          {moved.map((e) => (
            <li
              key={e.ticker}
              className="flex items-center gap-3 rounded-lg bg-[#12121f] border border-white/5 px-3 py-1.5"
            >
              <span className="font-semibold text-white w-16">{e.ticker}</span>
              <span
                className="text-[11px] rounded px-1.5 py-0.5"
                style={{
                  color: CLASS_COLOR[e.class],
                  border: `1px solid ${CLASS_COLOR[e.class]}55`,
                }}
              >
                {CLASS_LABEL[e.class]}
              </span>
              <span className="ml-auto flex items-center gap-2 text-xs">
                {sig(e.prevSignal)}
                <span className="text-gray-600">→</span>
                {sig(e.nextSignal)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
