"use client";

/**
 * Item #9 — engine-run health strip. A thin site-wide line reporting the
 * newest scan: "Last scan 2h ago · 950/954 OK · 4 failed", red when the
 * newest engine_runs row is failed/partial or older than ~26h. Without this,
 * if cron silently stops every page keeps rendering confident stale numbers
 * with no indication anything is wrong.
 */
import { useEffect, useState } from "react";
import { fetchEngineHealth, type EngineHealth } from "@/lib/api";

function ageLabel(hours: number | null): string {
  if (hours == null) return "unknown age";
  if (hours < 1) return `${Math.round(hours * 60)}m ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function EngineHealthStrip() {
  const [health, setHealth] = useState<EngineHealth | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    fetchEngineHealth()
      .then((h) => {
        if (active) setHealth(h);
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  // Nothing to say until we have data — don't flash an empty bar.
  if (!loaded || !health) return null;

  const bg = health.degraded ? "#D5000022" : "#00C85315";
  const border = health.degraded ? "#D50000" : "#00C85340";
  const dot = health.degraded ? "#D50000" : "#00C853";

  return (
    <div
      className="px-6 py-1.5 text-xs flex items-center gap-2 border-b"
      style={{ backgroundColor: bg, borderColor: border }}
      role="status"
    >
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: dot }}
      />
      <span className="text-gray-300">
        Last scan {ageLabel(health.ageHours)}
      </span>
      <span className="text-gray-500">·</span>
      <span className="text-gray-400">
        {health.symbolsOk}/{health.symbolsTotal} OK
      </span>
      {health.symbolsFailed > 0 && (
        <>
          <span className="text-gray-500">·</span>
          <span style={{ color: "#FF6D00" }}>
            {health.symbolsFailed} failed
          </span>
        </>
      )}
      {health.degraded && (
        <span className="ml-auto font-semibold" style={{ color: "#D50000" }}>
          {health.status === "running"
            ? "scan overdue"
            : `scan ${health.status}`}{" "}
          — signals may be stale
        </span>
      )}
    </div>
  );
}
