"use client";

/**
 * Runs once per app load: seeds the "All Symbols" universe into the local
 * Dexie DB if it isn't there yet. Renders nothing — this is a side effect
 * only, mirroring EngineHealthStrip's mount-and-fetch shape.
 */
import { useEffect } from "react";
import { ensureDefaultUniverse } from "@/lib/universe";

export function DefaultUniverseSeed() {
  useEffect(() => {
    ensureDefaultUniverse().catch(() => {
      // Best-effort — a failed seed shouldn't block the rest of the app.
    });
  }, []);

  return null;
}
