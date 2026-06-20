"use client";

/**
 * Invisible client component placed on the signal page. When the server-fetched
 * result mounts, it persists the run to the local DB + cookie and surfaces a
 * toast for any alerts that fired. Renders nothing itself.
 */
import { useEffect, useRef, useState } from "react";
import { useProfile } from "@/lib/useProfile";
import type { SignalDirection } from "@/lib/types";
import type { AlertRule } from "@/lib/db";

interface Props {
  ticker: string;
  period: string;
  resolvedPeriod: string;
  noLlm: boolean;
  signal: SignalDirection | null;
  confidence: number | null;
  aiDegraded: boolean;
}

export function RunRecorder(props: Props) {
  const { record, ready, profile } = useProfile();
  const [fired, setFired] = useState<AlertRule[]>([]);
  // Guard so React strict-mode double-invoke / re-renders don't double-record.
  const recordedKey = useRef<string | null>(null);

  useEffect(() => {
    if (!ready || !profile) return; // only record once onboarded
    const key = `${props.ticker}|${props.period}|${props.noLlm}|${props.signal}`;
    if (recordedKey.current === key) return;
    recordedKey.current = key;
    record({
      ticker: props.ticker,
      period: props.period,
      resolvedPeriod: props.resolvedPeriod,
      noLlm: props.noLlm,
      signal: props.signal,
      confidence: props.confidence,
      aiDegraded: props.aiDegraded,
    }).then(setFired);
  }, [ready, profile, props, record]);

  if (fired.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2 max-w-xs">
      {fired.map((a) => (
        <div
          key={a.id}
          className="rounded-lg bg-green-900/90 border border-green-500 text-green-100 text-sm px-4 py-3 shadow-lg"
          role="status"
        >
          🔔 Alert: <strong>{a.ticker}</strong> hit{" "}
          {a.direction.replace("_", " ")}
        </div>
      ))}
    </div>
  );
}
