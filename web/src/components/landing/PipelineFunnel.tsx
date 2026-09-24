"use client";

import { formatEt } from "@/lib/landing";
import { useLandingData } from "./LandingData";
import { EmptyState, Section, SkeletonBlock } from "./Shared";

export function PipelineFunnel() {
  const { loaded, funnel } = useLandingData();

  const rejectedPct =
    funnel && funnel.scanned > 0 ? Math.round((funnel.gated / funnel.scanned) * 100) : null;

  const stages = funnel
    ? [
        { label: "L1 Fetch", note: "yfinance OHLCV", count: funnel.total },
        { label: "L2 Indicators", note: "RSI, MACD, ADX, Bollinger…", count: funnel.scanned },
        { label: "L3 Detect", note: "18 detectors", count: funnel.scanned },
        { label: "L4 Vote", note: "confluence score", count: funnel.scanned },
        { label: "Gate", note: "no LLM spend on rejects", count: funnel.published },
        { label: "L5 LLM", note: "evidence + counter-evidence", count: funnel.published },
        { label: "L6 Store", note: "Supabase", count: funnel.published },
      ]
    : [];

  return (
    <Section testId="landing-pipeline" title="How a signal is made">
      {!loaded ? (
        <SkeletonBlock />
      ) : !funnel ? (
        <EmptyState>Run counts appear here after the first scan.</EmptyState>
      ) : (
        <div className="space-y-3">
          <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
            {stages.map((s) => (
              <li key={s.label} className="rounded-lg border border-white/5 p-2 text-center">
                <p className="text-lg font-bold text-white">{s.count.toLocaleString()}</p>
                <p className="text-xs font-semibold text-gray-300">{s.label}</p>
                <p className="text-[11px] text-gray-600">{s.note}</p>
              </li>
            ))}
          </ol>
          <p className="text-xs text-gray-500">
            {rejectedPct != null && (
              <>
                <span className="text-gray-300">{rejectedPct}% of scanned symbols</span> were
                rejected by the publication gate before any LLM call.{" "}
              </>
            )}
            {funnel.failed > 0 && `${funnel.failed} failed to fetch. `}
            Run {formatEt(funnel.finishedAt ?? funnel.startedAt) ?? "time unknown"}.
          </p>
        </div>
      )}
    </Section>
  );
}
