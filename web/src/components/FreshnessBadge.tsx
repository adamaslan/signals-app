import { classifyFreshness, FRESHNESS_COLORS } from "@/lib/freshness";

interface FreshnessBadgeProps {
  /** The bar the signal describes (epoch ms or ISO string). */
  barTs: number | string | null | undefined;
  /** When the row was computed/published — shown as a secondary line. */
  createdAt?: number | string | null;
}

function fmt(v: number | string | null | undefined): string | null {
  if (v == null) return null;
  const ts = typeof v === "string" ? new Date(v).getTime() : v;
  if (!Number.isFinite(ts)) return null;
  return new Date(ts).toLocaleString();
}

/**
 * Item #1 — freshness / staleness badge. Shows the age of the bar the signal
 * describes, so a 9-day-old "STRONG BUY" from a broken cron run can't look
 * identical to one computed 20 minutes ago.
 */
export function FreshnessBadge({ barTs, createdAt }: FreshnessBadgeProps) {
  const info = classifyFreshness(barTs);
  const color = FRESHNESS_COLORS[info.level];
  const barLabel = fmt(barTs);
  const computedLabel = fmt(createdAt);

  return (
    <span
      className="inline-flex flex-col rounded-lg px-2 py-1 text-xs"
      style={{ backgroundColor: color + "22", border: `1px solid ${color}` }}
      title={
        [
          barLabel ? `Bar: ${barLabel}` : null,
          computedLabel ? `Computed: ${computedLabel}` : null,
        ]
          .filter(Boolean)
          .join("\n") || undefined
      }
    >
      <span className="font-semibold" style={{ color }}>
        {info.label}
      </span>
      {computedLabel && (
        <span className="text-gray-500 text-[10px]">
          computed {computedLabel}
        </span>
      )}
    </span>
  );
}
