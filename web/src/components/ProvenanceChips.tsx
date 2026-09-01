import type { DivergencePattern } from "@/lib/types";

interface ProvenanceChipsProps {
  aiDegraded: boolean;
  /** feature_unavailable includes "llm_synthesis" when the scan ran rule-based. */
  noLlm: boolean;
  codeVersion?: string | null;
  /** When set and not aligned_*, shown as a named divergence chip. */
  divergencePattern?: DivergencePattern | null;
}

const DIVERGENCE_LABEL: Record<string, string> = {
  short_bull_long_bear: "Short-term strength inside a longer downtrend",
  short_bear_long_bull: "Short-term weakness inside a longer uptrend",
  mixed: "Mixed across timeframes",
  insufficient_data: "Not enough timeframes to judge alignment",
};

function Chip({
  text,
  color,
  title,
}: {
  text: string;
  color: string;
  title?: string;
}) {
  return (
    <span
      className="rounded-full text-[11px] px-2 py-0.5 font-medium"
      style={{ backgroundColor: color + "22", border: `1px solid ${color}`, color }}
      title={title}
    >
      {text}
    </span>
  );
}

/**
 * Items #4, #5, #8 — persistent provenance chips. `AI degraded` (the LLM
 * call failed and a fallback produced this number) and `Rule-based only`
 * are materially different products shown identically today; `code_version`
 * makes cross-ticker comparisons that mix engine versions visible; a named
 * divergence pattern is often the actual insight the headline direction hides.
 */
export function ProvenanceChips({
  aiDegraded,
  noLlm,
  codeVersion,
  divergencePattern,
}: ProvenanceChipsProps) {
  const showDivergence =
    divergencePattern != null &&
    divergencePattern !== "aligned_bullish" &&
    divergencePattern !== "aligned_bearish";

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {aiDegraded ? (
        <Chip
          text="AI degraded — fell back to rules"
          color="#FF6D00"
          title="The LLM synthesis call failed; a rule-based fallback produced this signal."
        />
      ) : noLlm ? (
        <Chip
          text="Rule-based only"
          color="#FFD740"
          title="This scan ran without LLM synthesis."
        />
      ) : (
        <Chip text="AI" color="#40C4FF" title="LLM-synthesized signal." />
      )}

      {showDivergence && (
        <Chip
          text={`Divergence: ${divergencePattern!.replace(/_/g, " ")}`}
          color="#B388FF"
          title={DIVERGENCE_LABEL[divergencePattern!] ?? divergencePattern!}
        />
      )}

      {codeVersion && (
        <Chip
          text={`engine ${codeVersion}`}
          color="#666"
          title="Detector engine version that produced this signal (provenance)."
        />
      )}
    </div>
  );
}
