"use client";

/**
 * Settings & data-ownership panel: edit name + defaults, export the entire
 * on-device DB as JSON, clear history, or wipe everything ("forget me").
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useProfile } from "@/lib/useProfile";
import { exportAll, wipeAll, clearHistory } from "@/lib/db";
import { PERIOD_OPTIONS } from "@/lib/types";
import { useAuth, signOut } from "@/lib/auth";
import { syncProfileToCloud, wipeCloudData } from "@/lib/sync";
import { AuthPanel } from "./AuthPanel";

export function SettingsPanel() {
  const router = useRouter();
  const { profile, patchProfile, forget } = useProfile();
  const { user } = useAuth();
  const [confirmWipe, setConfirmWipe] = useState(false);

  async function patchProfileAndSync(patch: Parameters<typeof patchProfile>[0]) {
    await patchProfile(patch);
    if (user) await syncProfileToCloud(user.id, patch);
  }

  if (!profile) {
    return (
      <p className="text-gray-500 text-sm">
        No profile on this device yet —{" "}
        <a href="/" className="text-green-500">
          set one up
        </a>
        .
      </p>
    );
  }

  async function handleExport() {
    const data = await exportAll();
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `signals-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleWipe() {
    // "Forget me" must actually forget: if signed in, wiping only the local
    // Dexie store would leave the cloud profile/watchlist rows in place —
    // and syncOnSignIn would silently repopulate the device from them on
    // the next sign-in, making the "cannot be undone" promise false.
    if (user) {
      await wipeCloudData(user.id);
      await signOut();
    }
    await wipeAll();
    await forget();
    router.push("/");
  }

  return (
    <div className="space-y-6">
      <AuthPanel />

      {/* Profile */}
      <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-4">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
          Profile
        </h2>

        <label className="block space-y-1">
          <span className="text-sm text-gray-400">Name</span>
          <input
            type="text"
            defaultValue={profile.name}
            onBlur={(e) => patchProfileAndSync({ name: e.target.value.trim() || "Trader" })}
            className="w-full rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-white text-sm focus:outline-none focus:border-white/30"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-gray-400">Default period</span>
          <select
            defaultValue={profile.defaultPeriod}
            onChange={(e) => patchProfileAndSync({ defaultPeriod: e.target.value })}
            className="w-full rounded-lg bg-[#12121f] border border-white/10 px-3 py-2 text-white text-sm focus:outline-none focus:border-white/30"
          >
            {PERIOD_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.longLabel}
                {!o.supported ? " (beta)" : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-3 cursor-pointer select-none">
          <input
            type="checkbox"
            defaultChecked={profile.defaultNoLlm}
            onChange={(e) => patchProfile({ defaultNoLlm: e.target.checked })}
            className="w-4 h-4 rounded border-white/20 bg-[#12121f] accent-green-500"
          />
          <span className="text-sm text-gray-400">
            Default to rule-based only (skip AI)
          </span>
        </label>

        <p className="text-xs text-gray-600">
          {profile.totalRuns} runs · first seen{" "}
          {new Date(profile.firstSeen).toLocaleDateString()}
        </p>
      </section>

      {/* Data ownership */}
      <section className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4 space-y-3">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
          Your Data
        </h2>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleExport}
            className="rounded-lg bg-[#12121f] border border-white/10 hover:border-white/30 text-gray-200 text-sm px-4 py-2 transition-colors"
          >
            ⬇ Export JSON
          </button>
          <button
            onClick={() => clearHistory()}
            className="rounded-lg bg-[#12121f] border border-white/10 hover:border-amber-600 text-amber-400 text-sm px-4 py-2 transition-colors"
          >
            Clear history
          </button>
        </div>

        {!confirmWipe ? (
          <button
            onClick={() => setConfirmWipe(true)}
            className="rounded-lg border border-red-800 text-red-400 hover:bg-red-950/40 text-sm px-4 py-2 transition-colors"
          >
            Forget me (wipe everything)
          </button>
        ) : (
          <div className="rounded-lg border border-red-800 bg-red-950/30 p-3 space-y-2">
            <p className="text-red-300 text-sm">
              This permanently deletes your profile, history, watchlist,
              presets, and alerts on this device
              {user ? ", and your synced profile/watchlist in the cloud" : ""}.
              This cannot be undone.
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleWipe}
                className="rounded-lg bg-red-700 hover:bg-red-600 text-white text-sm px-4 py-2"
              >
                Yes, wipe everything
              </button>
              <button
                onClick={() => setConfirmWipe(false)}
                className="rounded-lg border border-white/10 text-gray-400 text-sm px-4 py-2"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
