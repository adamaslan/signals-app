interface DataQualityMeterProps {
  score: number | null;
  reasons?: string[];
  /** Below this the caller should visually demote the whole card. */
  threshold?: number;
}

export const DATA_QUALITY_THRESHOLD = 0.7;

/** Is this score low enough that the signal card should be demoted? */
export function isLowDataQuality(
  score: number | null,
  threshold = DATA_QUALITY_THRESHOLD,
): boolean {
  return score != null && score < threshold;
}

/**
 * Item #2 — data-quality gate. Renders `data_quality_score` as a visible
 * meter; when it's below the threshold the bar goes red and the reasons are
 * shown inline, so a signal derived from gappy OHLCV can't look as
 * authoritative as a clean one.
 */
export function DataQualityMeter({
  score,
  reasons = [],
  threshold = DATA_QUALITY_THRESHOLD,
}: DataQualityMeterProps) {
  if (score == null) {
    return (
      <p className="text-xs text-gray-600">Data quality not reported</p>
    );
  }
  const pct = Math.round(score * 100);
  const low = score < threshold;
  const color = low ? "#D50000" : score < 0.85 ? "#FFD740" : "#00C853";

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-gray-500">
        <span>Data quality</span>
        <span style={{ color }}>{pct}%</span>
      </div>
      <div className="h-2 rounded-full bg-white/10 overflow-hidden">
        <div
          className="h-2 rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      {low && reasons.length > 0 && (
        <ul className="text-[11px] text-red-400/90 list-disc list-inside pt-1">
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
