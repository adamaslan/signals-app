"use client";

/**
 * React hook exposing the on-device profile plus a recorder for analysis runs.
 *
 * Bridges the IndexedDB store (db.ts) and the SSR cookie mirror (cookies.ts):
 * every run updates both, so the next server render greets the user correctly
 * and the rich local DB keeps the full history.
 */
import { useCallback, useEffect, useState } from "react";
import {
  ensureProfile,
  getProfile,
  recordRun,
  updateProfile,
  type Profile,
  type RecordRunInput,
  type AlertRule,
} from "./db";
import { writeProfileCookie, clearProfileCookie } from "./cookies";

export function useProfile() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    getProfile().then((p) => {
      if (!active) return;
      setProfile(p);
      setReady(true);
    });
    return () => {
      active = false;
    };
  }, []);

  /** Create / refresh the profile with a name (onboarding). */
  const initWithName = useCallback(async (name: string) => {
    const p = await ensureProfile(name.trim() || "Trader");
    setProfile(p);
    writeProfileCookie({
      name: p.name,
      lastTicker: p.lastTicker,
      lastSignal: p.lastSignal,
      lastSeen: p.lastSeen,
    });
    return p;
  }, []);

  const patchProfile = useCallback(async (patch: Partial<Profile>) => {
    await updateProfile(patch);
    setProfile((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  /**
   * Record an analysis run; refreshes profile state + the SSR cookie and
   * returns any alerts that fired so the caller can surface a notification.
   */
  const record = useCallback(
    async (input: RecordRunInput): Promise<AlertRule[]> => {
      const fired = await recordRun(input);
      const fresh = await getProfile();
      setProfile(fresh);
      if (fresh) {
        writeProfileCookie({
          name: fresh.name,
          lastTicker: fresh.lastTicker,
          lastSignal: fresh.lastSignal,
          lastSeen: fresh.lastSeen,
        });
      }
      return fired;
    },
    [],
  );

  const forget = useCallback(async () => {
    clearProfileCookie();
    setProfile(null);
  }, []);

  return { profile, ready, initWithName, patchProfile, record, forget };
}
