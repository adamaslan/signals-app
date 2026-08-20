"use client";

/**
 * Supabase Auth — magic-link (email OTP) sign-in, no password to manage.
 *
 * Phase 8 of docs/backend-state-and-supabase-plan.md. Signed-in state is
 * entirely optional: every feature in the app already works signed-out via
 * Dexie (db.ts). Signing in adds cross-device sync for profile/watchlist —
 * see sync.ts for the merge logic that runs on sign-in.
 */
import { useCallback, useEffect, useState } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase, supabaseConfigured } from "./supabase";

export interface AuthState {
  user: User | null;
  session: Session | null;
  /** True once the initial session check has completed. */
  ready: boolean;
}

/**
 * Send a magic-link sign-in email. Resolves once Supabase has accepted the
 * request — the user still has to click the link in their inbox.
 *
 * @throws Error if Supabase isn't configured or the request fails.
 */
export async function sendMagicLink(email: string): Promise<void> {
  if (!supabaseConfigured || !supabase) {
    throw new Error("Supabase is not configured");
  }
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo: typeof window !== "undefined" ? window.location.href : undefined,
    },
  });
  if (error) throw error;
}

export async function signOut(): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  await supabase.auth.signOut();
}

/**
 * React hook exposing the current Supabase Auth session, kept live via
 * onAuthStateChange (fires on sign-in, sign-out, and token refresh).
 */
export function useAuth(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!supabaseConfigured || !supabase) {
      setReady(true);
      return;
    }

    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setUser(data.session?.user ?? null);
      setReady(true);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      if (!active) return;
      setSession(newSession);
      setUser(newSession?.user ?? null);
    });

    return () => {
      active = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  return { user, session, ready };
}
