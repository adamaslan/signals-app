"use client";

import { useRouter } from "next/navigation";
import { PERIOD_OPTIONS, resolveBackendPeriod } from "@/lib/types";

interface PeriodSelectorProps {
  symbol: string;
  currentPeriod: string;
  noLlm: boolean;
}

/**
 * Compact full-range period switcher for the signal page header. Navigates to
 * the same symbol with a new period query param. Covers the entire UI range
 * (1m → MAX); unsupported periods carry a β marker and map server-side.
 */
export function PeriodSelector({
  symbol,
  currentPeriod,
  noLlm,
}: PeriodSelectorProps) {
  const router = useRouter();

  function handleChange(id: string) {
    router.push(`/signal/?symbol=${symbol}&period=${id}&no_llm=${noLlm}`);
  }

  return (
    <div className="flex items-center gap-2 flex-wrap justify-end">
      <span className="text-gray-500 text-xs uppercase tracking-widest shrink-0">
        Period
      </span>
      <div className="flex gap-1 flex-wrap">
        {PERIOD_OPTIONS.map((o) => {
          const isActive = o.id === currentPeriod;
          return (
            <button
              key={o.id}
              onClick={() => handleChange(o.id)}
              title={
                o.supported
                  ? o.longLabel
                  : `${o.longLabel} → ${resolveBackendPeriod(o.id)}`
              }
              className="rounded-lg text-xs px-2 py-1 font-medium transition-colors"
              style={{
                backgroundColor: isActive ? "#1a1a2e" : "transparent",
                color: isActive ? "#fff" : "#666",
                border: isActive
                  ? "1px solid rgba(255,255,255,0.15)"
                  : "1px solid transparent",
              }}
            >
              {o.label}
              {!o.supported && (
                <span className="ml-0.5 text-[8px] text-amber-500">β</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
