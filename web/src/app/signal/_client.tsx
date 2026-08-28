"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { fetchSignal, ApiError, SignalNotFoundError } from "@/lib/api";
import { SignalCard } from "@/components/SignalCard";
import { SignalMatrixRow } from "@/components/SignalMatrixRow";
import { EvidenceList } from "@/components/EvidenceList";
import { ConfluenceBar } from "@/components/ConfluenceBar";
import { PeriodSelector } from "@/components/PeriodSelector";
import { WatchlistButton } from "@/components/WatchlistButton";
import { RunRecorder } from "@/components/RunRecorder";
import { SignalHistoryPanel } from "@/components/SignalHistoryPanel";
import { SignalLineageTree } from "@/components/SignalLineageTree";
import { getPeriodOption, resolveBackendPeriod } from "@/lib/types";
import type { SignalOutput } from "@/lib/types";

function LoadingSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-8 w-32 rounded bg-white/5" />
      <div className="h-40 rounded-xl bg-white/5" />
      <div className="h-24 rounded-xl bg-white/5" />
      <div className="h-64 rounded-xl bg-white/5" />
    </div>
  );
}

interface SignalDashboardProps {
  symbol: string;
  period: string;
  noLlm: boolean;
}

function SignalDashboard({ symbol, period, noLlm }: SignalDashboardProps) {
  const backendPeriod = resolveBackendPeriod(period);
  const [data, setData] = useState<SignalOutput | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setErrorMsg(null);
    setNotFound(false);
    setData(null);

    fetchSignal(symbol, backendPeriod, noLlm)
      .then((result) => { if (active) setData(result); })
      .catch((err) => {
        if (!active) return;
        if (err instanceof SignalNotFoundError) setNotFound(true);
        else if (err instanceof ApiError) setErrorMsg(err.message);
        else if (err instanceof Error) setErrorMsg(err.message);
        else setErrorMsg("An unexpected error occurred.");
      })
      .finally(() => { if (active) setLoading(false); });

    return () => { active = false; };
  }, [symbol, backendPeriod, noLlm]);

  if (loading) return <LoadingSkeleton />;

  if (notFound) {
    return (
      <div
        className="rounded-xl border border-white/10 bg-[#1a1a2e] p-6 text-center space-y-3"
        role="status"
      >
        <p className="text-gray-300 font-semibold text-lg">Not scanned yet</p>
        <p className="text-gray-500 text-sm">
          {symbol} ({backendPeriod}) isn&apos;t in the current scan universe, or
          the last scan for it didn&apos;t clear the publication gate. Signals
          run on a schedule — check back after the next scan.
        </p>
      </div>
    );
  }

  if (errorMsg || !data) {
    return (
      <div
        className="rounded-xl border border-red-800 bg-red-950/30 p-6 text-center space-y-3"
        role="alert"
      >
        <p className="text-red-400 font-semibold text-lg">Failed to load signal</p>
        <p className="text-red-300/80 text-sm">{errorMsg}</p>
        <Link
          href={`/signal/?symbol=${symbol}&period=${period}&no_llm=${noLlm}`}
          className="inline-block mt-2 rounded-lg bg-red-800 hover:bg-red-700 text-white text-sm px-4 py-2 transition-colors"
        >
          Retry
        </Link>
      </div>
    );
  }

  const { signal, matrix, feature_unavailable, ticker } = data;
  const bullCount = matrix
    ? Object.values(matrix.signals).filter(
        (s) => s.direction === "strong_buy" || s.direction === "buy"
      ).length
    : 0;
  const bearCount = matrix
    ? Object.values(matrix.signals).filter(
        (s) => s.direction === "strong_sell" || s.direction === "sell"
      ).length
    : 0;
  const totalCount = matrix ? Object.values(matrix.signals).length : 0;
  const uiPeriodOpt = getPeriodOption(period);

  return (
    <div className="space-y-5">
      <RunRecorder
        ticker={ticker}
        period={period}
        resolvedPeriod={backendPeriod}
        noLlm={noLlm}
        signal={signal.direction}
        confidence={signal.confidence}
        aiDegraded={signal.ai_degraded}
      />

      {uiPeriodOpt && !uiPeriodOpt.supported && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-700 bg-amber-950/30 px-4 py-2 text-amber-400 text-sm">
          <span>ℹ</span>
          <span>
            {uiPeriodOpt.longLabel} isn&apos;t served by the backend yet —
            analysed the nearest range ({backendPeriod}).
          </span>
        </div>
      )}

      {signal.ai_degraded && (
        <div className="flex items-center gap-2 rounded-lg border border-orange-700 bg-orange-950/40 px-4 py-2 text-orange-400 text-sm">
          <span>⚠</span>
          <span>AI synthesis unavailable — showing rule-based fallback signal</span>
        </div>
      )}

      {feature_unavailable.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {feature_unavailable.map((f) => (
            <span
              key={f}
              className="rounded-full bg-yellow-900/40 border border-yellow-700 text-yellow-400 text-xs px-2 py-0.5"
            >
              {f.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}

      <SignalCard
        ticker={ticker}
        signal={signal}
        barTs={data.bar_ts}
        createdAt={data.created_at}
        dataQualityScore={data.data_quality_score}
        dataQualityReasons={data.data_quality_reasons}
      />

      {matrix && (
        <div className="rounded-xl bg-[#1a1a2e] p-4">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-3">
            Multi-Timeframe Matrix
          </h2>
          <SignalMatrixRow matrix={matrix} />
        </div>
      )}

      {matrix && totalCount > 0 && (
        <div className="rounded-xl bg-[#1a1a2e] p-4">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-3">
            Confluence
          </h2>
          <ConfluenceBar
            bullCount={bullCount}
            bearCount={bearCount}
            total={totalCount}
            alignmentScore={matrix.alignment_score}
          />
        </div>
      )}

      <div className="rounded-xl bg-[#1a1a2e] p-4">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-3">
          Evidence ({signal.evidence.items.length} items)
        </h2>
        <EvidenceList items={signal.evidence.items} direction={signal.direction} />
      </div>

      <div className="rounded-xl bg-[#1a1a2e] p-4">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-3">
          Signal Lineage
        </h2>
        <SignalLineageTree ticker={ticker} />
      </div>

      <div className="rounded-xl bg-[#1a1a2e] p-4">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-3">
          History
        </h2>
        <SignalHistoryPanel ticker={ticker} />
      </div>

      <div className="text-xs text-gray-600 space-x-3 pt-1">
        <span>Prompt: {signal.prompt_version}</span>
        <span>·</span>
        <span>Schema: {data.schema_version}</span>
        <span>·</span>
        <span>Period: {uiPeriodOpt?.longLabel ?? period}</span>
        {noLlm && <span className="text-yellow-700">· No-LLM mode</span>}
      </div>
    </div>
  );
}

function MissingSymbol() {
  return (
    <div className="rounded-xl border border-white/10 bg-[#1a1a2e] p-6 text-center space-y-3">
      <p className="text-gray-300 font-semibold text-lg">No ticker specified</p>
      <p className="text-gray-500 text-sm">
        This page needs a <code className="text-gray-400">?symbol=</code> query param.
      </p>
      <Link
        href="/"
        className="inline-block mt-2 rounded-lg bg-white/10 hover:bg-white/20 text-white text-sm px-4 py-2 transition-colors"
      >
        ← Back to search
      </Link>
    </div>
  );
}

export function SignalPageClient() {
  const searchParams = useSearchParams();
  const symbol = searchParams.get("symbol")?.toUpperCase() ?? null;
  const period = searchParams.get("period") ?? "3mo";
  const noLlm = searchParams.get("no_llm") === "true";

  if (!symbol) return <MissingSymbol />;

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Link href="/" className="text-gray-500 hover:text-gray-300 text-sm transition-colors">
              ← Home
            </Link>
          </div>
          <div className="flex items-center gap-3 mt-1">
            <h1 className="text-3xl font-extrabold tracking-tight text-white">{symbol}</h1>
            <WatchlistButton ticker={symbol} />
          </div>
        </div>
        <PeriodSelector symbol={symbol} currentPeriod={period} noLlm={noLlm} />
      </div>

      <SignalDashboard symbol={symbol} period={period} noLlm={noLlm} />
    </div>
  );
}
