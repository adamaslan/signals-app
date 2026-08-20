"use client";

/**
 * Sign-in / account panel for Settings. Magic-link email auth (Supabase's
 * signInWithOtp) — no password to manage. Entirely optional: the app works
 * fully signed-out via the local Dexie store (db.ts). Signing in adds
 * cross-device sync of profile/watchlist (see lib/sync.ts).
 */
import { useEffect, useRef, useState } from "react";
import { useAuth, sendMagicLink, signOut } from "@/lib/auth";
import { syncOnSignIn } from "@/lib/sync";
import { supabaseConfigured } from "@/lib/supabase";

export function AuthPanel() {
  const { user, ready } = useAuth();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const syncedForUserId = useRef<string | null>(null);

  // Run the one-time merge exactly once per sign-in, not on every render.
  useEffect(() => {
    if (!user || syncedForUserId.current === user.id) return;
    syncedForUserId.current = user.id;
    syncOnSignIn(user.id).catch((err) => {
      console.error("sync-on-sign-in failed:", err);
    });
  }, [user]);

  if (!supabaseConfigured) {
    return (
      <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-2">
          Account
        </h2>
        <p className="text-gray-500 text-sm">
          Cloud sync isn&apos;t configured for this build.
        </p>
      </section>
    );
  }

  if (!ready) {
    return (
      <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-2">
          Account
        </h2>
        <div className="h-8 rounded bg-white/5 animate-pulse" />
      </section>
    );
  }

  if (user) {
    return (
      <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
          Account
        </h2>
        <p className="text-sm text-gray-300">
          Signed in as <span className="text-white font-medium">{user.email}</span>
        </p>
        <p className="text-xs text-gray-500">
          Your profile and watchlist sync across devices while signed in.
        </p>
        <button
          onClick={() => signOut()}
          className="rounded-lg bg-[#12121f] border border-white/10 hover:border-white/30 text-gray-300 text-sm px-4 py-2 transition-colors"
        >
          Sign out
        </button>
      </section>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSending(true);
    try {
      await sendMagicLink(email.trim());
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send sign-in link.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
      <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
        Account
      </h2>
      <p className="text-xs text-gray-500">
        Sign in to sync your profile and watchlist across devices. Your data
        keeps working locally if you never sign in.
      </p>

      {sent ? (
        <p className="text-sm text-green-400">
          Check <span className="text-white">{email}</span> for a sign-in link.
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="flex-1 rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-white text-sm focus:outline-none focus:border-white/30"
          />
          <button
            type="submit"
            disabled={sending}
            className="rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white text-sm px-4 py-2 transition-colors whitespace-nowrap"
          >
            {sending ? "Sending…" : "Send link"}
          </button>
        </form>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}
    </section>
  );
}
