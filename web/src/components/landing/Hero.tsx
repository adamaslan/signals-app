"use client";

import { TIMEFRAMES } from "@/lib/types";
import { formatEt, aiDegradedShare, AI_DEGRADED_BANNER_SHARE, newestBarTs } from "@/lib/landing";
import { useLandingData } from "./LandingData";

const DETECTOR_COUNT = 18;

export function Hero() {
  const { loaded, funnel, health, signals } = useLandingData();

  const runLabel = health
    ? `last run ${formatEt(health.finishedAt ?? health.startedAt) ?? "unknown"}`
    : loaded
      ? "no run data yet"
      : "loading…";
  const okLabel = health ? ` (${health.symbolsOk}/${health.symbolsTotal} ok)` : "";
  const tickers = funnel?.total ?? health?.symbolsTotal;

  const stale = health?.degraded ?? false;
  const llmDegraded =
    signals != null && aiDegradedShare(signals) >= AI_DEGRADED_BANNER_SHARE;
  const barLabel = signals ? formatEt(newestBarTs(signals)) : null;

  return (
    <div data-testid="landing-hero" className="text-center space-y-3">
      <h1 className="text-4xl font-extrabold tracking-tight text-white">
        Market Signal Analysis
      </h1>
      <p className="text-gray-400 text-base max-w-xl mx-auto">
        {DETECTOR_COUNT} technical detectors vote on every ticker, a gate throws out
        the weak opinions, and an AI writes the evidence for what survives.
      </p>
      <p data-testid="landing-proof-line" className="text-gray-500 text-xs">
        {tickers ? `${tickers} tickers · ` : ""}
        {DETECTOR_COUNT} detectors · {TIMEFRAMES.length} timeframes · {runLabel}
        {okLabel}
        {barLabel ? ` · newest bar ${barLabel}` : ""}
      </p>
      {(stale || llmDegraded) && (
        <p
          data-testid="landing-degraded-banner"
          role="alert"
          className="mx-auto max-w-xl rounded-lg px-3 py-1.5 text-xs"
          style={{ backgroundColor: "#D5000022", border: "1px solid #D50000", color: "#ff8a80" }}
        >
          {stale && "The newest scan is stale or failed — signals below may be out of date. "}
          {llmDegraded && "AI narrative synthesis ran degraded on many signals."}
        </p>
      )}
    </div>
  );
}
