"use client";

import Link from "next/link";
import { useMemo } from "react";
import { pickTopSignals } from "@/lib/api";
import { classifyFreshness, FRESHNESS_COLORS } from "@/lib/freshness";
import { SIGNAL_COLORS, SIGNAL_LABELS } from "@/lib/types";
import type { LandingSignal } from "@/lib/api";
import { useLandingData } from "./LandingData";
import { EmptyState, Section, SkeletonBlock } from "./Shared";

const PER_DIRECTION = 5;

function Row({ s }: { s: LandingSignal }) {
  const fresh = classifyFreshness(s.barTs);
  const pct = s.confidence != null ? Math.round(s.confidence * 100) : null;
  return (
    <li>
      <Link
        href={`/signal/?symbol=${s.ticker}&period=3mo`}
        className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-white/5"
      >
        <span className="w-16 font-bold text-white">{s.ticker}</span>
        <span className="text-xs font-semibold" style={{ color: SIGNAL_COLORS[s.direction] }}>
          {SIGNAL_LABELS[s.direction]}
        </span>
        <span className="ml-auto text-xs text-gray-400">
          {pct != null ? `${pct}% conf` : "conf n/a"}
          {s.confluenceScore != null && ` · score ${s.confluenceScore.toFixed(2)}`}
        </span>
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: FRESHNESS_COLORS[fresh.level] }}
          title={fresh.label}
        />
      </Link>
    </li>
  );
}

export function TopSignals() {
  const { loaded, signals } = useLandingData();
  const top = useMemo(
    () => (signals ? pickTopSignals(signals, PER_DIRECTION) : null),
    [signals],
  );

  return (
    <Section testId="landing-top-signals" title="Today's strongest signals">
      {!loaded ? (
        <SkeletonBlock />
      ) : !top || (top.bullish.length === 0 && top.bearish.length === 0) ? (
        <EmptyState>
          No published signals yet — the engine hasn&apos;t run, or Supabase isn&apos;t configured.
        </EmptyState>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <p className="mb-1 text-xs text-gray-500">Bullish</p>
            <ul>{top.bullish.map((s) => <Row key={s.ticker} s={s} />)}</ul>
          </div>
          <div>
            <p className="mb-1 text-xs text-gray-500">Bearish</p>
            <ul>{top.bearish.map((s) => <Row key={s.ticker} s={s} />)}</ul>
          </div>
        </div>
      )}
    </Section>
  );
}
