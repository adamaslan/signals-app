"use client";

import { useState } from "react";
import type { Signal } from "@/lib/types";
import { SIGNAL_COLORS, SIGNAL_LABELS } from "@/lib/types";
import type { DivergencePattern } from "@/lib/types";
import { EvidenceList } from "./EvidenceList";
import { FreshnessBadge } from "./FreshnessBadge";
import { DataQualityMeter, isLowDataQuality } from "./DataQualityMeter";
import { ProvenanceChips } from "./ProvenanceChips";
import { CalibrationHint } from "./CalibrationHint";

interface SignalCardProps {
  ticker: string;
  signal: Signal;
  /** The bar this signal describes (epoch ms / ISO) — drives the freshness
   * badge (§5 item #1). */
  barTs?: number | string | null;
  /** When the row was computed/published. */
  createdAt?: number | string | null;
  /** 0–1 data-quality score — drives the meter and card demotion (item #2). */
  dataQualityScore?: number | null;
  dataQualityReasons?: string[];
  /** true when the scan ran rule-based (feature_unavailable has llm_synthesis). */
  noLlm?: boolean;
  /** Detector engine version — provenance chip (#8). */
  codeVersion?: string | null;
  /** Cross-timeframe divergence pattern — named chip (#5). */
  divergencePattern?: DivergencePattern | null;
}

/**
 * Web port of gcp3-mobile/components/SignalCard.tsx.
 * Renders the primary synthesized signal with collapsible evidence, a
 * freshness badge, and a data-quality meter that visually demotes the card
 * when input quality is poor.
 */
export function SignalCard({
  ticker,
  signal,
  barTs,
  createdAt,
  dataQualityScore = null,
  dataQualityReasons = [],
  noLlm = false,
  codeVersion = null,
  divergencePattern = null,
}: SignalCardProps) {
  const [expanded, setExpanded] = useState(false);

  const color = SIGNAL_COLORS[signal.direction];
  const confidencePct = Math.round(signal.confidence * 100);
  const lowQuality = isLowDataQuality(dataQualityScore);

  // Card opacity and border scale with confidence — mirrored from mobile.
  // A low data-quality score desaturates the whole card further, so a
  // signal built on gappy data can't look as authoritative as a clean one.
  const cardOpacity = (0.3 + 0.7 * signal.confidence) * (lowQuality ? 0.6 : 1);
  const borderWidth = 1 + 4 * signal.confidence;

  return (
    <div
      data-testid="signal-card"
      data-ticker={ticker}
      className="rounded-xl p-5 space-y-3 transition-all"
      style={{
        backgroundColor: "#1a1a2e",
        borderColor: color,
        borderWidth: `${borderWidth}px`,
        borderStyle: "solid",
        opacity: cardOpacity,
      }}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <span className="text-white text-xl font-bold">{ticker}</span>
        <div className="flex items-center gap-2">
          <FreshnessBadge barTs={barTs} createdAt={createdAt} />
          {signal.ai_degraded && (
            <span
              className="rounded-lg text-xs px-2 py-0.5 font-medium"
              style={{
                backgroundColor: "#FF6D0033",
                border: "1px solid #FF6D00",
                color: "#FF6D00",
              }}
            >
              ⚠ AI unavailable
            </span>
          )}
        </div>
      </div>

      {/* Direction label */}
      <p className="text-2xl font-extrabold tracking-wide" style={{ color }}>
        {SIGNAL_LABELS[signal.direction]}
      </p>

      <ProvenanceChips
        aiDegraded={signal.ai_degraded}
        noLlm={noLlm}
        codeVersion={codeVersion}
        divergencePattern={divergencePattern}
      />

      {(dataQualityScore != null || dataQualityReasons.length > 0) && (
        <DataQualityMeter
          score={dataQualityScore}
          reasons={dataQualityReasons}
        />
      )}

      {/* Confidence pill */}
      <div className="flex items-center gap-3">
        <span
          className="rounded-full text-sm font-semibold px-3 py-1"
          style={{ backgroundColor: color + "33", color }}
        >
          {confidencePct}%
        </span>
        {/* Confidence progress bar */}
        <div className="flex-1 h-2 rounded-full bg-white/10 overflow-hidden">
          <div
            className="h-2 rounded-full transition-all"
            style={{ width: `${confidencePct}%`, backgroundColor: color }}
          />
        </div>
      </div>

      {/* Timeframe + prompt version meta + calibration */}
      <p className="text-xs text-gray-500">
        {signal.timeframe} · {signal.prompt_version}
        <CalibrationHint direction={signal.direction} />
      </p>

      {/* Expand evidence toggle */}
      <button
        onClick={() => setExpanded((e) => !e)}
        className="text-sm text-gray-400 hover:text-gray-200 underline underline-offset-2 transition-colors"
        aria-expanded={expanded}
      >
        {expanded
          ? "Hide evidence"
          : `Show ${signal.evidence.items.length} evidence items`}
      </button>

      {expanded && (
        <div className="pt-2 border-t border-white/5">
          <EvidenceList items={signal.evidence.items} direction={signal.direction} />
        </div>
      )}
    </div>
  );
}
