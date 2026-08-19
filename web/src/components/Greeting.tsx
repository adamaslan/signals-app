"use client";

/**
 * Personalised greeting. Reads the on-device profile (IndexedDB) once mounted;
 * shows last-used time and a one-tap link to re-run the last ticker.
 */
import Link from "next/link";
import { useProfile } from "@/lib/useProfile";

function lastSeenLabel(ts: number): string {
  const d = Math.floor((Date.now() - ts) / 86400000);
  if (d <= 0) return "today";
  if (d === 1) return "yesterday";
  return `${d} days ago`;
}

export function Greeting() {
  const { profile } = useProfile();
  if (!profile) return null;

  return (
    <div className="text-center space-y-1">
      <p className="text-gray-300">
        Welcome back, <span className="font-semibold text-white">{profile.name}</span>
      </p>
      <p className="text-gray-600 text-xs">
        Last here {lastSeenLabel(profile.lastSeen)} · {profile.totalRuns} run
        {profile.totalRuns === 1 ? "" : "s"} on this device
        {profile.lastTicker && (
          <>
            {" · "}
            <Link
              href={`/signal/?symbol=${profile.lastTicker}&period=${profile.defaultPeriod}&no_llm=${profile.defaultNoLlm}`}
              className="text-green-500 hover:text-green-400"
            >
              re-run {profile.lastTicker} →
            </Link>
          </>
        )}
      </p>
    </div>
  );
}
