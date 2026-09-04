"use client";

import { useState } from "react";
import type { TimeframeMatrix, Timeframe, Signal } from "@/lib/types";
import { SIGNAL_COLORS, SIGNAL_ARROWS, TIMEFRAMES } from "@/lib/types";
import { EvidenceList } from "./EvidenceList";

interface SignalMatrixRowProps {
  matrix: TimeframeMatrix;
}

/**
 * Web port of gcp3-mobile/components/SignalMatrixRow.tsx.
 * Renders a row of 6 timeframe cells, an alignment bar, and optionally
 * expands the evidence for a clicked cell.
 */
export function SignalMatrixRow({ matrix }: SignalMatrixRowProps) {
  const [activeTimeframe, setActiveTimeframe] = useState<Timeframe | null>(null);

  const hasDivergence =
    matrix.divergence_pattern !== "aligned_bullish" &&
    matrix.divergence_pattern !== "aligned_bearish";

  const alignPct = Math.round(matrix.alignment_score * 100);

  const alignBarColor =
    matrix.alignment_score > 0.7
      ? "#00C853"
      : matrix.alignment_score < 0.4
      ? "#D50000"
      : "#FFD740";

  const activeSignal: Signal | null =
    activeTimeframe ? (matrix.signals[activeTimeframe] ?? null) : null;

  return (
    <div data-testid="signal-matrix-row" data-ticker={matrix.ticker} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {/* Ticker label */}
        <span className="text-white text-sm font-bold w-12 shrink-0">
          {matrix.ticker}
        </span>

        {/* 6-cell timeframe matrix */}
        <div className="flex gap-1 flex-wrap">
          {TIMEFRAMES.map((tf) => {
            const sig = matrix.signals[tf];
            if (!sig) return null;

            const color = SIGNAL_COLORS[sig.direction];
            const conf = sig.confidence;
            const isActive = activeTimeframe === tf;
            // Cell padding scales with confidence — mirrored from mobile
            const pad = `${4 + conf * 4}px`;

            return (
              <button
                key={tf}
                onClick={() => setActiveTimeframe(isActive ? null : tf)}
                aria-label={`${tf}: ${sig.direction.replace(/_/g, " ")}, ${Math.round(conf * 100)}% confidence`}
                aria-pressed={isActive}
                className="rounded-lg flex flex-col items-center justify-center min-w-[38px] transition-all"
                style={{
                  backgroundColor: color + "22",
                  border: `${isActive ? 2 : 1}px solid ${isActive ? color : color + "88"}`,
                  padding: pad,
                }}
              >
                <span className="text-[9px] font-semibold text-gray-400">{tf}</span>
                <span className="text-base" style={{ color }}>
                  {SIGNAL_ARROWS[sig.direction]}
                </span>
              </button>
            );
          })}
        </div>

        {/* Alignment bar */}
        <div className="flex items-center gap-1 ml-auto shrink-0">
          <div className="w-10 h-1.5 rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-1.5 rounded-full transition-all"
              style={{ width: `${alignPct}%`, backgroundColor: alignBarColor }}
            />
          </div>
          <span className="text-[10px] text-gray-500">{alignPct}%</span>
        </div>

        {hasDivergence && (
          <span title={`Divergence: ${matrix.divergence_pattern.replace(/_/g, " ")}`}>
            💡
          </span>
        )}
      </div>

      {/* Divergence interpretation */}
      {hasDivergence && matrix.divergence_interpretation && (
        <p className="text-xs text-gray-400 italic pl-14">
          {matrix.divergence_interpretation}
        </p>
      )}

      {/* Expanded evidence for active timeframe */}
      {activeSignal && (
        <div className="rounded-lg bg-white/5 p-3 space-y-2">
          <p className="text-xs text-gray-500 font-semibold uppercase tracking-widest">
            {activeTimeframe} evidence
          </p>
          <EvidenceList items={activeSignal.evidence.items} direction={activeSignal.direction} />
        </div>
      )}
    </div>
  );
}
