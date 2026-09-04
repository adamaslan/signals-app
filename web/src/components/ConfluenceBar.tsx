interface ConfluenceBarProps {
  bullCount: number;
  bearCount: number;
  total: number;
  alignmentScore: number;
}

/**
 * Visualizes the bull vs bear signal counts and the overall alignment score.
 */
export function ConfluenceBar({
  bullCount,
  bearCount,
  total,
  alignmentScore,
}: ConfluenceBarProps) {
  const bullPct = total > 0 ? Math.round((bullCount / total) * 100) : 0;
  const bearPct = total > 0 ? Math.round((bearCount / total) * 100) : 0;
  const neutralCount = total - bullCount - bearCount;
  const neutralPct = 100 - bullPct - bearPct;
  const alignPct = Math.round(alignmentScore * 100);

  const alignColor =
    alignmentScore > 0.7 ? "#00C853" : alignmentScore < 0.4 ? "#D50000" : "#FFD740";

  return (
    <div data-testid="confluence-bar" className="space-y-4">
      {/* Bull / Bear / Neutral breakdown */}
      <div className="space-y-2">
        <div className="flex justify-between text-xs text-gray-500 font-medium">
          <span>Bull</span>
          <span>Neutral</span>
          <span>Bear</span>
        </div>

        {/* Segmented bar */}
        <div className="h-4 rounded-full overflow-hidden flex bg-white/5">
          {bullPct > 0 && (
            <div
              className="h-full rounded-l-full transition-all"
              style={{ width: `${bullPct}%`, backgroundColor: "#00C853" }}
              title={`${bullCount} bullish`}
            />
          )}
          {neutralPct > 0 && (
            <div
              className="h-full transition-all"
              style={{ width: `${neutralPct}%`, backgroundColor: "#FFD740" }}
              title={`${neutralCount} neutral`}
            />
          )}
          {bearPct > 0 && (
            <div
              className="h-full rounded-r-full transition-all"
              style={{ width: `${bearPct}%`, backgroundColor: "#D50000" }}
              title={`${bearCount} bearish`}
            />
          )}
        </div>

        {/* Counts */}
        <div className="flex justify-between text-xs">
          <span style={{ color: "#00C853" }}>
            {bullCount} bull ({bullPct}%)
          </span>
          <span className="text-gray-600">
            {neutralCount} hold
          </span>
          <span style={{ color: "#D50000" }}>
            {bearCount} bear ({bearPct}%)
          </span>
        </div>
      </div>

      {/* Alignment score */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-gray-500">
          <span>Timeframe alignment</span>
          <span style={{ color: alignColor }}>{alignPct}%</span>
        </div>
        <div className="h-2 rounded-full bg-white/10 overflow-hidden">
          <div
            className="h-2 rounded-full transition-all"
            style={{ width: `${alignPct}%`, backgroundColor: alignColor }}
          />
        </div>
      </div>
    </div>
  );
}
