"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { fetchSignal, LANDING_PERIOD } from "@/lib/api";
import { pickFeatured } from "@/lib/landing";
import { SIGNAL_COLORS, SIGNAL_LABELS, type SignalOutput } from "@/lib/types";
import { EvidenceList } from "@/components/EvidenceList";
import { SignalMatrixRow } from "@/components/SignalMatrixRow";
import { useLandingData } from "./LandingData";
import { EmptyState, Section, SkeletonBlock, useInView } from "./Shared";

const MAX_SUPPORTING = 3;
const MAX_COUNTER = 1;

export function FeaturedDeepDive() {
  const { loaded, signals } = useLandingData();
  const featured = useMemo(() => (signals ? pickFeatured(signals) : null), [signals]);
  const [output, setOutput] = useState<SignalOutput | null>(null);
  const [fetched, setFetched] = useState(false);
  // Below the fold: only spend the extra query once the section is near view.
  const [sectionRef, nearView] = useInView<HTMLElement>();

  const ticker = featured?.ticker;
  useEffect(() => {
    if (!ticker || !nearView) return;
    let active = true;
    fetchSignal(ticker, LANDING_PERIOD, false)
      .then((o) => active && setOutput(o))
      .catch(() => {})
      .finally(() => active && setFetched(true));
    return () => {
      active = false;
    };
  }, [ticker, nearView]);

  const items = output
    ? [
        ...output.signal.evidence.items.filter((i) => !i.is_counter).slice(0, MAX_SUPPORTING),
        ...output.signal.evidence.items.filter((i) => i.is_counter).slice(0, MAX_COUNTER),
      ]
    : [];

  return (
    <Section
      testId="landing-featured"
      sectionRef={sectionRef}
      title="Featured deep dive"
      aside={
        ticker && (
          <Link
            href={`/signal/?symbol=${ticker}&period=${LANDING_PERIOD}`}
            className="text-xs text-gray-500 hover:text-gray-300"
          >
            Full analysis →
          </Link>
        )
      }
    >
      {!loaded || (ticker && !fetched) ? (
        <SkeletonBlock height="h-40" />
      ) : !ticker || !output ? (
        <EmptyState>A featured ticker appears here once signals are published.</EmptyState>
      ) : (
        <div className="space-y-4">
          <p className="text-white">
            <span className="text-xl font-bold">{output.ticker}</span>{" "}
            <span
              className="text-sm font-semibold"
              style={{ color: SIGNAL_COLORS[output.signal.direction] }}
            >
              {SIGNAL_LABELS[output.signal.direction]}
            </span>{" "}
            <span className="text-xs text-gray-500">
              highest-confidence signal in the latest scan
            </span>
          </p>
          {output.matrix && <SignalMatrixRow matrix={output.matrix} />}
          {items.length > 0 && (
            <EvidenceList items={items} direction={output.signal.direction} />
          )}
        </div>
      )}
    </Section>
  );
}
