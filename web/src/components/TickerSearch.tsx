"use client";

import { useState, useEffect, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useLiveQuery } from "dexie-react-hooks";
import { db, saveConfig, deleteConfig } from "@/lib/db";
import { useProfile } from "@/lib/useProfile";
import { PeriodControlPanel } from "./PeriodControlPanel";
import { getPeriodOption } from "@/lib/types";

export function TickerSearch() {
  const router = useRouter();
  const { profile, ready, initWithName } = useProfile();
  const [symbol, setSymbol] = useState("");
  const [period, setPeriod] = useState("3mo");
  const [noLlm, setNoLlm] = useState(false);

  // Apply profile defaults once it loads.
  useEffect(() => {
    if (profile) {
      setPeriod(profile.defaultPeriod);
      setNoLlm(profile.defaultNoLlm);
    }
  }, [profile]);

  const configs = useLiveQuery(
    async () => (db ? db.savedConfigs.orderBy("createdAt").reverse().toArray() : []),
    [],
    [],
  );

  async function go(ticker: string) {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    // No name gate in front of search: a default on-device profile is created
    // on the first analysis so run history still records.
    if (ready && !profile) await initWithName("");
    router.push(`/signal/?symbol=${t}&period=${period}&no_llm=${noLlm}`);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void go(symbol);
  }

  async function handleSaveConfig() {
    const opt = getPeriodOption(period);
    const name = `${opt?.label ?? period}${noLlm ? " · rules" : ""}`;
    await saveConfig(name, period, noLlm);
  }

  return (
    <div className="w-full max-w-md space-y-5">
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="Enter ticker (e.g. AAPL)"
          className="w-full rounded-xl bg-[#1a1a2e] border border-white/10 px-4 py-3 text-white text-lg placeholder-gray-600 focus:outline-none focus:border-white/30 transition-colors"
          autoFocus
          autoComplete="off"
          spellCheck={false}
        />

        <PeriodControlPanel
          period={period}
          onPeriodChange={setPeriod}
          noLlm={noLlm}
          onNoLlmChange={setNoLlm}
        />

        <div className="flex gap-2">
          <button
            type="submit"
            disabled={!symbol.trim()}
            className="flex-1 rounded-xl bg-green-700 hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold py-3 transition-colors"
          >
            Analyze
          </button>
          <button
            type="button"
            onClick={handleSaveConfig}
            title="Save this period + mode as a reusable preset"
            className="rounded-xl bg-[#1a1a2e] border border-white/10 hover:border-white/30 text-gray-300 px-4 transition-colors"
          >
            ＋ Preset
          </button>
        </div>
      </form>

      {/* Saved config presets */}
      {configs && configs.length > 0 && (
        <div className="space-y-2">
          <p className="text-gray-600 text-xs uppercase tracking-widest">Presets</p>
          <div className="flex flex-wrap gap-2">
            {configs.map((c) => (
              <span
                key={c.id}
                className="inline-flex items-center gap-1.5 rounded-full bg-[#1a1a2e] border border-white/10 text-gray-300 text-sm pl-3 pr-1.5 py-1"
              >
                <button
                  onClick={() => {
                    setPeriod(c.period);
                    setNoLlm(c.noLlm);
                  }}
                  className="hover:text-white"
                >
                  {c.name}
                </button>
                <button
                  onClick={() => c.id != null && deleteConfig(c.id)}
                  className="text-gray-600 hover:text-red-400 text-xs"
                  title="Delete preset"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
