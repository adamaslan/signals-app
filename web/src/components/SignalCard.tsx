"use client";

import { useState } from "react";
import type { Signal } from "@/lib/types";
import { SIGNAL_COLORS, SIGNAL_LABELS } from "@/lib/types";
import { EvidenceList } from "./EvidenceList";

interface SignalCardProps {
  ticker: string;
  signal: Signal;
}

/**
 * Web port of gcp3-mobile/components/SignalCard.tsx.
 * Renders the primary synthesized signal with collapsible evidence.
 */
export function SignalCard({ ticker, signal }: SignalCardProps) {
  const [expanded, setExpanded] = useState(false);

  const color = SIGNAL_COLORS[signal.direction];
  const confidencePct = Math.round(signal.confidence * 100);

  // Card opacity and border scale with confidence — mirrored from mobile
  const cardOpacity = 0.3 + 0.7 * signal.confidence;
  const borderWidth = 1 + 4 * signal.confidence;

  return (
    <div
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
      <div className="flex items-center justify-between">
        <span className="text-white text-xl font-bold">{ticker}</span>
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

      {/* Direction label */}
      <p className="text-2xl font-extrabold tracking-wide" style={{ color }}>
        {SIGNAL_LABELS[signal.direction]}
      </p>

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

      {/* Timeframe + prompt version meta */}
      <p className="text-xs text-gray-500">
        {signal.timeframe} · {signal.prompt_version}
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
