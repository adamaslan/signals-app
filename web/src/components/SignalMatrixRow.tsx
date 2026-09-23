"use client";

import { useState } from "react";
import type { TimeframeMatrix, Timeframe, Signal } from "@/lib/types";
import { SIGNAL_COLORS, SIGNAL_ARROWS, TIMEFRAMES } from "@/lib/types";
import { EvidenceList } from "./EvidenceList";

interface SignalMatrixRowProps {
  matrix: TimeframeMatrix;
}

/** Swing vs long-term grouping for the fixed matrix layout (§B1) — mirrors
 * PERIOD_OPTIONS' `group` field. Kept as its own map (rather than deriving
 * from PERIOD_OPTIONS at render time) because Timeframe ids ("1D") and
 * PeriodOption ids ("1d") don't line up 1:1. */
const TIMEFRAME_GROUP: Record<Timeframe, "swing" | "long"> = {
  "1D": "swing",
  "5D": "swing",
  "1M": "swing",
  "3M": "swing",
  "6M": "long",
  "1Y": "long",
};

const SWING_TIMEFRAMES = TIMEFRAMES.filter((tf) => TIMEFRAME_GROUP[tf] === "swing");
const LONG_TIMEFRAMES = TIMEFRAMES.filter((tf) => TIMEFRAME_GROUP[tf] === "long");

interface MatrixCellProps {
  tf: Timeframe;
  sig: Signal | undefined;
  isActive: boolean;
  onClick: () => void;
}

function MatrixCell({ tf, sig, isActive, onClick }: MatrixCellProps) {
  if (!sig) {
    return (
      <div
        aria-label={`${tf}: no signal`}
        title="No signal for this timeframe — either not yet computed, or fewer bars than this window needs"
        className="rounded-lg flex flex-col items-center justify-center min-w-[38px] px-1.5 py-1 border border-dashed border-white/15 text-gray-600"
      >
        <span className="text-[9px] font-semibold">{tf}</span>
        <span className="text-[10px]">n/a</span>
      </div>
    );
  }

  const color = SIGNAL_COLORS[sig.direction];
  const conf = sig.confidence;
  // Cell padding scales with confidence — mirrored from mobile.
  const pad = `${4 + conf * 4}px`;

  return (
    <button
      onClick={onClick}
      aria-label={`${tf}: ${sig.direction.replace(/_/g, " ")}, ${Math.round(conf * 100)}% confidence`}
      aria-pressed={isActive}
      data-tf-slot={tf}
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
      <span className="text-[9px] text-gray-500">{Math.round(conf * 100)}%</span>
    </button>
  );
}

/**
 * Web port of gcp3-mobile/components/SignalMatrixRow.tsx.
 *
 * Renders a fixed-width row of TIMEFRAMES.length timeframe cells (currently
 * 6; grows automatically if TIMEFRAMES gains entries — see the "Long-term"
 * companion work extending timeframes to 1Y/5Y/MAX on
 * feat/trigger-universe-scan), grouped Swing | Long-term with a divider, an
 * alignment bar, and an optional expanded-evidence panel for a clicked
 * cell. A timeframe with no signal renders as a dashed "n/a" slot instead of
 * silently disappearing (§B1 / F8) — the old `if (!sig) return null` made
 * 1D/5D vanish for every ticker under < 20 bars and shifted the whole row's
 * layout ticker to ticker.
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
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {/* Ticker label */}
        <span className="text-white text-sm font-bold w-12 shrink-0">
          {matrix.ticker}
        </span>

        {/* Fixed-slot timeframe matrix, Swing | Long-term */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <div className="flex gap-1">
            {SWING_TIMEFRAMES.map((tf) => (
              <MatrixCell
                key={tf}
                tf={tf}
                sig={matrix.signals[tf]}
                isActive={activeTimeframe === tf}
                onClick={() =>
                  setActiveTimeframe(activeTimeframe === tf ? null : tf)
                }
              />
            ))}
          </div>
          <div className="h-8 w-px bg-white/10" aria-hidden />
          <div className="flex gap-1">
            {LONG_TIMEFRAMES.map((tf) => (
              <MatrixCell
                key={tf}
                tf={tf}
                sig={matrix.signals[tf]}
                isActive={activeTimeframe === tf}
                onClick={() =>
                  setActiveTimeframe(activeTimeframe === tf ? null : tf)
                }
              />
            ))}
          </div>
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
