"use client";

/**
 * "Suggested backtests" — asks the engine (`POST /backtest/suggest`) which
 * claims this basket's live signals are making, lists them as hypotheses,
 * and runs any of them in place (`POST /backtest/run` with the hypothesis's
 * focus) to show a supported / contradicted / inconclusive verdict.
 *
 * Suggestions are fetched on click, not on mount: each one costs a yfinance
 * fetch + detector pass per ticker on the local backend.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  labHref,
  runEngineBacktest,
  suggestBacktests,
  type EngineBacktest,
  type Hypothesis,
  type Suggestions,
  type VerdictStatus,
} from "@/lib/backtestLab";
import { EngineBacktestView, VerdictBadge } from "./EngineBacktestView";

const KIND_LABEL: Record<string, string> = {
  cluster: "cluster",
  single: "per-ticker",
  conflict: "conflict",
  category: "category",
  strength: "label check",
};

interface RunState {
  loading: boolean;
  result: EngineBacktest | null;
  error: string | null;
  open: boolean;
}

const IDLE: RunState = { loading: false, result: null, error: null, open: false };

function HypothesisCard({
  h,
  state,
  onRun,
  onToggle,
  showLabLink,
}: {
  h: Hypothesis;
  state: RunState;
  onRun: () => void;
  onToggle: () => void;
  showLabLink: boolean;
}) {
  return (
    <li className="rounded-lg border border-white/10 bg-[#12121f] p-3 space-y-2">
      <div className="flex flex-wrap items-start gap-2">
        <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-gray-400">
          {KIND_LABEL[h.kind] ?? h.kind}
        </span>
        <span className="flex-1 min-w-0 text-sm text-white">{h.title}</span>
        {state.result?.verdict && <VerdictBadge status={state.result.verdict.status} />}
      </div>
      <p className="text-xs text-gray-400 leading-relaxed">{h.rationale}</p>
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
        <span>
          {h.symbols.length} ticker{h.symbols.length === 1 ? "" : "s"} · {h.horizonDays}-bar horizon ·{" "}
          {h.period}
        </span>
        <span className="ml-auto flex items-center gap-2">
          {showLabLink && (
            <Link
              href={labHref({
                symbols: h.symbols,
                horizonDays: h.horizonDays,
                period: h.period,
                focus: h.focus,
              })}
              className="text-gray-500 hover:text-gray-300 underline"
            >
              open in lab
            </Link>
          )}
          {state.result && (
            <button onClick={onToggle} className="text-gray-500 hover:text-gray-300 underline">
              {state.open ? "hide details" : "details"}
            </button>
          )}
          <button
            onClick={onRun}
            disabled={state.loading}
            className="rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-40 px-2.5 py-1 text-white"
          >
            {state.loading ? "testing…" : state.result ? "re-run" : "Run test"}
          </button>
        </span>
      </div>
      {state.error && <p className="text-xs text-red-400">{state.error}</p>}
      {state.result && !state.open && state.result.verdict && (
        <p className="text-xs text-gray-400">{state.result.verdict.message}</p>
      )}
      {state.result && state.open && (
        <div className="pt-2 border-t border-white/5">
          <EngineBacktestView result={state.result} focus={h.focus} />
        </div>
      )}
    </li>
  );
}

export function SuggestedBacktests({
  symbols,
  autoLoad = false,
  showLabLink = true,
  horizonDays = null,
}: {
  symbols: string[];
  /** Fetch suggestions immediately (the lab does; embedded panels don't). */
  autoLoad?: boolean;
  showLabLink?: boolean;
  /** Force one horizon for every suggestion; null = per-signal default. */
  horizonDays?: number | null;
}) {
  const [sug, setSug] = useState<Suggestions | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [runningAll, setRunningAll] = useState(false);
  const autoLoaded = useRef(false);

  async function load() {
    if (symbols.length === 0) return;
    setErr(null);
    setLoading(true);
    setRuns({});
    try {
      setSug(await suggestBacktests(symbols, { horizonDays }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "suggest failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (autoLoad && !autoLoaded.current && symbols.length > 0) {
      autoLoaded.current = true;
      void load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoLoad, symbols.join(",")]);

  function patch(id: string, next: Partial<RunState>) {
    setRuns((prev) => ({ ...prev, [id]: { ...(prev[id] ?? IDLE), ...next } }));
  }

  async function run(h: Hypothesis) {
    patch(h.id, { loading: true, error: null });
    try {
      const result = await runEngineBacktest({
        symbols: h.symbols,
        period: h.period,
        horizonDays: h.horizonDays,
        focus: h.focus,
      });
      patch(h.id, { loading: false, result });
    } catch (e) {
      patch(h.id, { loading: false, error: e instanceof Error ? e.message : "backtest failed" });
    }
  }

  async function runAll() {
    if (!sug) return;
    setRunningAll(true);
    // Sequential on purpose: each run is CPU-bound on the local backend.
    for (const h of sug.hypotheses) {
      if (!runs[h.id]?.result) await run(h);
    }
    setRunningAll(false);
  }

  const tally = sug
    ? sug.hypotheses.reduce<Record<string, number>>((acc, h) => {
        const s = runs[h.id]?.result?.verdict?.status;
        if (s) acc[s] = (acc[s] ?? 0) + 1;
        return acc;
      }, {})
    : {};

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={load}
          disabled={loading || symbols.length === 0}
          className="rounded-lg bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-white text-xs px-3 py-1.5"
          title="Detect each ticker's live signals and propose backtests that test them"
        >
          {loading ? "asking the engine…" : sug ? "refresh suggestions" : "Suggest backtests"}
        </button>
        {sug && sug.hypotheses.length > 0 && (
          <button
            onClick={runAll}
            disabled={runningAll}
            className="rounded-lg bg-white/10 hover:bg-white/20 disabled:opacity-40 text-white text-xs px-3 py-1.5"
          >
            {runningAll ? "testing all…" : "Run all"}
          </button>
        )}
        {Object.keys(tally).length > 0 && (
          <span className="flex items-center gap-1 text-[11px] text-gray-500">
            {Object.entries(tally).map(([s, n]) => (
              <span key={s} className="flex items-center gap-1">
                <VerdictBadge status={s as VerdictStatus} />×{n}
              </span>
            ))}
          </span>
        )}
      </div>

      {err && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-3 py-2 text-red-400 text-xs">
          {err}
        </div>
      )}

      {sug && (
        <>
          {sug.symbolsFailed.length > 0 && (
            <p className="text-xs text-amber-500">
              Couldn&apos;t read live signals for{" "}
              {sug.symbolsFailed.map((f) => f.symbol).join(", ")}.
            </p>
          )}
          {sug.hypotheses.length === 0 ? (
            <p className="text-sm text-gray-500">
              No directional signals are live on these tickers right now — nothing to test.
            </p>
          ) : (
            <ul className="space-y-2">
              {sug.hypotheses.map((h) => (
                <HypothesisCard
                  key={h.id}
                  h={h}
                  state={runs[h.id] ?? IDLE}
                  onRun={() => run(h)}
                  onToggle={() => patch(h.id, { open: !runs[h.id]?.open })}
                  showLabLink={showLabLink}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
