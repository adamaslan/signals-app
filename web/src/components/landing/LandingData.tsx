"use client";

/**
 * One shared fetch for the landing showcase. Sections read from context so
 * first paint costs a handful of queries (signals, funnel, health) rather
 * than one per section.
 */
import { createContext, useContext, useEffect, useState } from "react";
import {
  fetchEngineHealth,
  fetchLandingSignals,
  fetchPipelineFunnel,
  type EngineHealth,
  type LandingSignal,
  type PipelineFunnel,
} from "@/lib/api";

export interface LandingData {
  /** False until the first round of queries settles. */
  loaded: boolean;
  signals: LandingSignal[] | null;
  funnel: PipelineFunnel | null;
  health: EngineHealth | null;
}

const EMPTY: LandingData = {
  loaded: false,
  signals: null,
  funnel: null,
  health: null,
};

const LandingContext = createContext<LandingData>(EMPTY);

export function useLandingData(): LandingData {
  return useContext(LandingContext);
}

export function LandingDataProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<LandingData>(EMPTY);

  useEffect(() => {
    let active = true;
    Promise.all([fetchLandingSignals(), fetchPipelineFunnel(), fetchEngineHealth()])
      .then(([signals, funnel, health]) => {
        if (active) setData({ loaded: true, signals, funnel, health });
      })
      .catch(() => {
        if (active) setData({ ...EMPTY, loaded: true });
      });
    return () => {
      active = false;
    };
  }, []);

  return <LandingContext.Provider value={data}>{children}</LandingContext.Provider>;
}
