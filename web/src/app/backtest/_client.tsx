"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  BACKTEST_HORIZONS,
  BACKTEST_PERIODS,
  MAX_BACKTEST_SYMBOLS,
  parseFocusParam,
  parseTickers,
  runEngineBacktest,
  type EngineBacktest,
  type Focus,
} from "@/lib/backtestLab";
import { EngineBacktestView } from "@/components/EngineBacktestView";
import { SuggestedBacktests } from "@/components/SuggestedBacktests";
import { ErrorBoundary } from "@/components/ErrorBoundary";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-4">
      <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">{title}</h2>
      {children}
    </section>
  );
}

export function BacktestLabClient() {
  const params = useSearchParams();
  const initialSymbols = parseTickers(params.get("symbols") ?? "");
  const initialFocus = parseFocusParam(params.get("focus"));
  const hParam = Number(params.get("h"));
  const periodParam = params.get("period");

  const [tickerText, setTickerText] = useState(initialSymbols.join(", "));
  const [period, setPeriod] = useState<string>(
    periodParam && (BACKTEST_PERIODS as readonly string[]).includes(periodParam) ? periodParam : "2y",
  );
  const [horizon, setHorizon] = useState<number>(
    Number.isFinite(hParam) && hParam >= 1 && hParam <= 60 ? hParam : 20,
  );
  const [focus, setFocus] = useState<Focus[]>(initialFocus);
  const [result, setResult] = useState<EngineBacktest | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [suggestFor, setSuggestFor] = useState<string[] | null>(
    params.get("suggest") === "1" && initialSymbols.length ? initialSymbols : null,
  );
  const autoRan = useRef(false);

  const tickers = parseTickers(tickerText);
  const overCap = tickers.length > MAX_BACKTEST_SYMBOLS;

  async function run() {
    if (tickers.length === 0) return;
    setErr(null);
    setRunning(true);
    try {
      setResult(await runEngineBacktest({ symbols: tickers, period, horizonDays: horizon, focus }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "backtest failed");
    } finally {
      setRunning(false);
    }
  }

  // A deep link that carries a hypothesis (focus) runs it straight away —
  // that's the "open in lab" path from a suggestion card.
  useEffect(() => {
    if (!autoRan.current && initialSymbols.length > 0 && initialFocus.length > 0) {
      autoRan.current = true;
      void run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-6">
      <Link href="/" className="text-gray-500 hover:text-gray-300 text-sm transition-colors">
        ← Home
      </Link>
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-white">Backtest Lab</h1>
        <p className="text-gray-500 text-sm mt-1">
          Replay every detector over years of daily bars and see which signals actually called the
          next move on your tickers — or let the engine propose the hypotheses worth testing from
          what&apos;s firing right now. Runs on the local backend (<code>scripts/run_local.sh</code>).
        </p>
      </div>

      <Section title="Run a backtest">
        <div className="space-y-3">
          <textarea
            value={tickerText}
            onChange={(e) => setTickerText(e.target.value)}
            rows={2}
            placeholder="Tickers — AAPL, MSFT, NVDA …"
            className="w-full rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-green-600 resize-y"
          />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="text-gray-500">History</label>
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="rounded-lg bg-[#12121f] border border-white/10 px-2 py-1 text-white"
            >
              {BACKTEST_PERIODS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <label className="text-gray-500 ml-2">Horizon</label>
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              className="rounded-lg bg-[#12121f] border border-white/10 px-2 py-1 text-white"
            >
              {Array.from(new Set([...BACKTEST_HORIZONS, horizon]))
                .sort((a, b) => a - b)
                .map((h) => (
                  <option key={h} value={h}>
                    {h} bars
                  </option>
                ))}
            </select>
            <button
              onClick={run}
              disabled={running || tickers.length === 0 || overCap}
              className="rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-40 text-white px-3 py-1.5"
            >
              {running ? "replaying…" : "Run backtest"}
            </button>
            <button
              onClick={() => setSuggestFor(tickers)}
              disabled={tickers.length === 0}
              className="rounded-lg bg-white/10 hover:bg-white/20 disabled:opacity-40 text-white px-3 py-1.5"
            >
              Suggest hypotheses
            </button>
            <span className="text-gray-600">
              {tickers.length} ticker{tickers.length === 1 ? "" : "s"}
            </span>
          </div>
          {overCap && (
            <p className="text-xs text-amber-500">
              A backtest replays every bar per ticker, so it&apos;s capped at {MAX_BACKTEST_SYMBOLS}{" "}
              tickers. Suggestions still work on up to 100.
            </p>
          )}
          {focus.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-gray-500">Testing hypothesis on:</span>
              {focus.map((f) => (
                <span
                  key={`${f.group}:${f.key}`}
                  className="rounded bg-blue-900/40 border border-blue-700/50 px-2 py-0.5 text-blue-200"
                >
                  {f.group}: {f.key}
                </span>
              ))}
              <button
                onClick={() => setFocus([])}
                className="text-gray-500 hover:text-gray-300 underline"
              >
                clear
              </button>
            </div>
          )}
          {err && (
            <div className="rounded-lg border border-red-800 bg-red-950/30 px-3 py-2 text-red-400 text-xs">
              {err}
            </div>
          )}
        </div>
        {result && (
          <ErrorBoundary label="Backtest result">
            <EngineBacktestView result={result} focus={focus} />
          </ErrorBoundary>
        )}
      </Section>

      {suggestFor && (
        <Section title="Engine-suggested hypotheses">
          <p className="text-xs text-gray-500">
            From the signals live on the latest bar of {suggestFor.length} ticker
            {suggestFor.length === 1 ? "" : "s"}. Each is a claim the engine is making today; running
            it checks whether that claim has held historically.
          </p>
          <ErrorBoundary label="Suggested hypotheses">
            <SuggestedBacktests
              key={suggestFor.join(",")}
              symbols={suggestFor}
              autoLoad
              showLabLink={false}
            />
          </ErrorBoundary>
        </Section>
      )}
    </div>
  );
}
