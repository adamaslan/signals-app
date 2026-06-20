import type { EvidenceItem, SignalDirection } from "@/lib/types";
import { SIGNAL_COLORS } from "@/lib/types";

interface EvidenceListProps {
  items: EvidenceItem[];
  direction: SignalDirection;
}

const SOURCE_LABELS: Record<string, string> = {
  technical: "Technical",
  fundamental: "Fundamental",
  macro: "Macro",
  news_sentiment: "News Sentiment",
  options_flow: "Options Flow",
  sector_relative: "Sector Relative",
  cross_asset: "Cross-Asset",
  earnings: "Earnings",
  rule_based: "Rule-Based",
};

/**
 * Renders evidence items with weight bars.
 * Counter-evidence items are visually distinguished.
 */
export function EvidenceList({ items, direction }: EvidenceListProps) {
  const signalColor = SIGNAL_COLORS[direction];
  const supporting = items.filter((i) => !i.is_counter);
  const counter = items.filter((i) => i.is_counter);

  return (
    <div className="space-y-4">
      {/* Supporting evidence */}
      {supporting.length > 0 && (
        <div className="space-y-2">
          {supporting.map((item, idx) => (
            <EvidenceItemRow
              key={`sup-${idx}`}
              item={item}
              barColor={signalColor}
            />
          ))}
        </div>
      )}

      {/* Counter evidence */}
      {counter.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-gray-600 uppercase tracking-widest font-semibold">
            Counter-evidence
          </p>
          {counter.map((item, idx) => (
            <EvidenceItemRow key={`ctr-${idx}`} item={item} barColor="#FF6D00" isCounter />
          ))}
        </div>
      )}
    </div>
  );
}

function EvidenceItemRow({
  item,
  barColor,
  isCounter = false,
}: {
  item: EvidenceItem;
  barColor: string;
  isCounter?: boolean;
}) {
  const weightPct = Math.round(item.weight * 100);

  return (
    <div className={`rounded-lg p-3 space-y-1.5 ${isCounter ? "bg-orange-950/20" : "bg-white/5"}`}>
      {/* Source label + weight */}
      <div className="flex items-center justify-between gap-2">
        <span
          className="text-xs font-semibold rounded-full px-2 py-0.5"
          style={{
            backgroundColor: barColor + "22",
            color: barColor,
          }}
        >
          {SOURCE_LABELS[item.source] ?? item.source}
        </span>
        <span className="text-xs text-gray-500 shrink-0">{weightPct}%</span>
      </div>

      {/* Weight bar */}
      <div className="h-1 rounded-full bg-white/10 overflow-hidden">
        <div
          className="h-1 rounded-full transition-all"
          style={{ width: `${weightPct}%`, backgroundColor: barColor }}
        />
      </div>

      {/* Summary */}
      <p className="text-sm text-gray-300 leading-snug">{item.summary}</p>
    </div>
  );
}
