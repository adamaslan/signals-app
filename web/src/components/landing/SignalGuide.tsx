"use client";

/**
 * "The signals, explained" — every detector the engine runs, what it watches,
 * the exact labels it can fire, and how its votes become a BUY / SELL / HOLD.
 * Static content from lib/signalGlossary; no data fetch, so it renders the
 * same whether or not the engine has run.
 */
import { useState } from "react";
import {
  DETECTORS,
  FAMILIES,
  FAMILY_ORDER,
  SCORING_RULES,
  detectorsByFamily,
  isPairedLabel,
  signalLabelCount,
  type DetectorEntry,
  type FiredSignal,
  type SignalFamily,
} from "@/lib/signalGlossary";

type Filter = SignalFamily | "all";

const WEIGHT_LABEL = ["no vote", "normal vote", "strong vote", "extreme vote"] as const;

const STEPS = [
  {
    title: "Detect",
    body: `${DETECTORS.length} detectors scan the latest bar and fire any of ~${signalLabelCount()} labelled signals.`,
  },
  {
    title: "Vote",
    body: "Each signal votes bull or bear, weighted 1 (normal), 2 (strong) or 3 (extreme). Crosses, MACD and volume get a small bonus. The net becomes a score from −1 to +1.",
  },
  {
    title: "Gate",
    body: `BUY needs a score of +${SCORING_RULES.buyThreshold} or more and at least ${SCORING_RULES.minAgreeingSignals} agreeing signals. SELL is the mirror image. Anything weaker is HOLD, and so is anything with under ${Math.round(SCORING_RULES.minDataQuality * 100)}% data quality. HOLDs are never published.`,
  },
  {
    title: "Explain",
    body: "An AI writes the evidence and the counter-evidence for each call that passes. If it fails, the card says Rule-Based and shows the raw score instead.",
  },
];

const TERMS = [
  { term: "score", def: "the net detector vote, −1 (all bearish) to +1 (all bullish)" },
  { term: "conf", def: "the AI's confidence in the call. A flat 55% means the AI step failed and the rule-based fallback filled it in" },
  { term: "Rule-Based", def: "no AI narrative for this signal; the call comes from the vote alone" },
];

function SideMark({ s }: { s: FiredSignal }) {
  if (s.side === "none") {
    return <span className="text-gray-500" title="Carries no direction">●</span>;
  }
  if (isPairedLabel(s.label)) {
    return (
      <span title="Fires either way" className="whitespace-nowrap">
        <span className="text-emerald-400">▲</span>
        <span className="text-rose-400">▼</span>
      </span>
    );
  }
  return s.side === "bull" ? (
    <span className="text-emerald-400" title="Bullish vote">▲</span>
  ) : (
    <span className="text-rose-400" title="Bearish vote">▼</span>
  );
}

function WeightDots({ weight }: { weight: number }) {
  return (
    <span className="inline-flex gap-0.5" title={WEIGHT_LABEL[weight]} aria-label={WEIGHT_LABEL[weight]}>
      {[1, 2, 3].map((i) => (
        <span
          key={i}
          className={`h-1.5 w-1.5 rounded-full ${i <= weight ? "bg-gray-300" : "bg-white/10"}`}
        />
      ))}
    </span>
  );
}

function DetectorCard({ d, open, onToggle }: { d: DetectorEntry; open: boolean; onToggle: () => void }) {
  const fam = FAMILIES[d.family];
  return (
    <li className="flex flex-col rounded-2xl border border-white/[0.06] bg-card/80 p-4 shadow-lg shadow-black/20">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${fam.tone}`}>
            {fam.label}
          </span>
          <h3 className="mt-2 text-sm font-semibold text-white">{d.name}</h3>
        </div>
        <span className="shrink-0 pt-1 text-[11px] text-gray-500">
          {d.signals.length} signal{d.signals.length === 1 ? "" : "s"}
        </span>
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-gray-400">{d.watches}</p>

      <ul className="mt-3 space-y-1.5">
        {d.signals.map((s) => (
          <li key={s.label} className="text-xs">
            <div className="flex items-center gap-2">
              <SideMark s={s} />
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-200" title={s.label}>
                {s.label}
              </span>
              <WeightDots weight={s.weight} />
            </div>
            {open && <p className="ml-6 mt-0.5 text-[11px] text-gray-500">{s.when}</p>}
          </li>
        ))}
      </ul>

      {open && (
        <p className="mt-3 rounded-lg border border-white/[0.05] bg-white/[0.02] p-2.5 text-xs leading-relaxed text-gray-300">
          <span className="font-semibold text-indigo-300">How to read it: </span>
          {d.readIt}
        </p>
      )}

      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="mt-auto pt-3 text-left text-xs font-medium text-indigo-300 hover:text-indigo-200"
      >
        {open ? "Hide triggers ↑" : "Show triggers & how to read it ↓"}
      </button>
    </li>
  );
}

export function SignalGuide() {
  const [filter, setFilter] = useState<Filter>("all");
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const shown = detectorsByFamily(filter);
  const allOpen = shown.every((d) => openIds.has(d.id));

  function toggle(id: string) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setOpenIds((prev) => {
      const next = new Set(prev);
      for (const d of shown) {
        if (allOpen) next.delete(d.id);
        else next.add(d.id);
      }
      return next;
    });
  }

  const chips: { key: Filter; label: string; count: number }[] = [
    { key: "all", label: "All", count: DETECTORS.length },
    ...FAMILY_ORDER.map((f) => ({ key: f, label: FAMILIES[f].label, count: detectorsByFamily(f).length })),
  ];

  return (
    <section id="signals-explained" data-testid="landing-signal-guide" className="scroll-mt-24 space-y-6">
      <div className="space-y-1 text-center">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-indigo-300/80">
          The signals, explained
        </p>
        <h2 className="text-2xl font-bold tracking-tight text-white">
          What the {DETECTORS.length} detectors actually look at
        </h2>
        <p className="mx-auto max-w-2xl text-sm text-gray-400">
          Every BUY or SELL on this site comes from these detectors voting. Here is what each one
          watches, the exact labels it can fire, and where it tends to mislead.
        </p>
      </div>

      <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, i) => (
          <li key={step.title} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs font-semibold text-indigo-300">
              {i + 1} · {step.title}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-gray-400">{step.body}</p>
          </li>
        ))}
      </ol>

      <dl className="flex flex-wrap justify-center gap-x-6 gap-y-1 text-xs text-gray-500">
        {TERMS.map((t) => (
          <div key={t.term} className="flex gap-1.5">
            <dt className="font-mono text-gray-300">{t.term}</dt>
            <dd>= {t.def}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Filter detectors by family" className="flex flex-wrap gap-2">
          {chips.map((c) => (
            <button
              key={c.key}
              type="button"
              role="tab"
              aria-selected={filter === c.key}
              onClick={() => setFilter(c.key)}
              className={`rounded-full border px-3 py-1 text-xs transition ${
                filter === c.key
                  ? "border-indigo-400/50 bg-indigo-500/20 text-white"
                  : "border-white/10 bg-white/[0.03] text-gray-400 hover:border-white/20 hover:text-gray-200"
              }`}
            >
              {c.label} <span className="text-gray-500">{c.count}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-4 text-[11px] text-gray-500">
          <span>
            <span className="text-emerald-400">▲</span> bull <span className="text-rose-400">▼</span> bear{" "}
            <span>●</span> no side
          </span>
          <button type="button" onClick={toggleAll} className="font-medium text-indigo-300 hover:text-indigo-200">
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        </div>
      </div>

      {filter !== "all" && (
        <p className="text-center text-xs text-gray-400">{FAMILIES[filter].blurb}</p>
      )}

      <ul className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {shown.map((d) => (
          <DetectorCard key={d.id} d={d} open={openIds.has(d.id)} onToggle={() => toggle(d.id)} />
        ))}
      </ul>
    </section>
  );
}
