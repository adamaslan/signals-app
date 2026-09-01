"use client";

/**
 * Syncs the local Dexie profile/watchlist (db.ts) with Supabase's
 * `profiles`/`watchlist` tables (Phase 8) for signed-in users.
 *
 * Dexie stays the source of truth for signed-out use and for fields
 * Supabase's simpler schema doesn't carry (firstSeen, lastTicker,
 * totalRuns, per-item note/targetPrice beyond `note`, etc.) — this module
 * only syncs the fields that exist on both sides. Signed-out users are
 * completely unaffected: every function here is a no-op without a session.
 */
import { supabase, supabaseConfigured } from "./supabase";
import { db, getProfile, updateProfile, type Profile, type Universe } from "./db";

interface CloudUniverse {
  id: string;
  name: string;
  note: string;
  tickers: string[];
  default_period: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

interface CloudProfile {
  id: string;
  display_name: string | null;
  default_period: string;
  theme: string;
}

interface CloudWatchItem {
  user_id: string;
  ticker: string;
  note: string | null;
  added_at: string;
}

/**
 * Run once right after sign-in: merges local Dexie state with whatever's
 * already in Supabase for this user.
 *
 * Merge policy, deliberately simple: if a cloud profile already exists
 * (this user has signed in before, possibly on another device), it wins —
 * the local device adopts the cloud profile's synced fields. If no cloud
 * profile exists yet (first sign-in), the local profile is pushed up to
 * create one. Either way, after this call both sides agree.
 */
export async function syncOnSignIn(userId: string): Promise<void> {
  if (!supabaseConfigured || !supabase) return;

  const { data: cloudProfile } = await supabase
    .from("profiles")
    .select("id,display_name,default_period,theme")
    .eq("id", userId)
    .maybeSingle<CloudProfile>();

  const localProfile = await getProfile();

  if (cloudProfile) {
    // Cloud wins — pull it down onto this device.
    if (localProfile) {
      await updateProfile({
        name: cloudProfile.display_name ?? localProfile.name,
        defaultPeriod: cloudProfile.default_period,
        theme: (cloudProfile.theme as Profile["theme"]) ?? localProfile.theme,
      });
    }
  } else if (localProfile) {
    // First sign-in on any device — push the local profile up.
    await supabase.from("profiles").insert({
      id: userId,
      display_name: localProfile.name,
      default_period: localProfile.defaultPeriod,
      theme: localProfile.theme,
    });
  }

  await mergeWatchlist(userId);
  await mergeUniverses(userId);
}

/**
 * Merge device-local `universes` with the cloud `universes` table on
 * sign-in. Mirrors mergeWatchlist's union-and-never-delete stance, with a
 * revision-based tiebreak per the design doc §6.2:
 *
 * - name present only on one side  → copy it to the other
 * - name on both sides             → the higher `revision` wins its
 *   tickers/note/period; on a tie, cloud wins (consistent with
 *   syncOnSignIn's "cloud wins" merge)
 *
 * Runs and backtest caches are deliberately NOT synced — derived,
 * device-specific, and large.
 */
async function mergeUniverses(userId: string): Promise<void> {
  if (!supabaseConfigured || !supabase || !db) return;

  const [localList, cloudResult] = await Promise.all([
    db.universes.toArray(),
    supabase
      .from("universes")
      .select(
        "id,name,note,tickers,default_period,revision,created_at,updated_at",
      )
      .eq("user_id", userId),
  ]);
  const cloudList = (cloudResult.data ?? []) as CloudUniverse[];

  const key = (n: string) => n.trim().toLowerCase();
  const localByKey = new Map(localList.map((u) => [key(u.name), u]));
  const cloudByKey = new Map(cloudList.map((u) => [key(u.name), u]));

  // Push local-only universes up.
  for (const u of localList) {
    if (cloudByKey.has(key(u.name))) continue;
    await supabase.from("universes").insert({
      user_id: userId,
      name: u.name,
      note: u.note,
      tickers: u.tickers,
      default_period: u.defaultPeriod,
      revision: u.revision,
    });
  }

  // Pull cloud-only universes down, and reconcile ones on both sides.
  for (const c of cloudList) {
    const local = localByKey.get(key(c.name));
    if (!local) {
      await db.universes.add({
        name: c.name,
        note: c.note,
        tickers: c.tickers,
        defaultPeriod: c.default_period,
        defaultNoLlm: false,
        createdAt: new Date(c.created_at).getTime(),
        updatedAt: new Date(c.updated_at).getTime(),
        revision: c.revision,
        coverage: null,
      });
      continue;
    }
    // Both sides have it — the higher revision wins; tie → cloud.
    if (c.revision >= local.revision) {
      if (
        c.revision !== local.revision ||
        c.tickers.join(",") !== local.tickers.join(",") ||
        c.note !== local.note
      ) {
        await db.universes.update(local.id!, {
          note: c.note,
          tickers: c.tickers,
          defaultPeriod: c.default_period,
          revision: c.revision,
          updatedAt: Date.now(),
          coverage: null,
        });
      }
    } else {
      // Local is ahead — push it up.
      await supabase
        .from("universes")
        .update({
          note: local.note,
          tickers: local.tickers,
          default_period: local.defaultPeriod,
          revision: local.revision,
          updated_at: new Date().toISOString(),
        })
        .eq("user_id", userId)
        .eq("name", c.name);
    }
  }
}

/**
 * Mirror a local universe create/update to Supabase, if signed in.
 * Fire-and-forget, same pattern as syncWatchlistAdd. Call after any
 * universe.ts mutation while a session exists.
 */
export async function syncUniverseUp(
  userId: string,
  u: Pick<
    Universe,
    "name" | "note" | "tickers" | "defaultPeriod" | "revision"
  >,
): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  await supabase.from("universes").upsert(
    {
      user_id: userId,
      name: u.name,
      note: u.note,
      tickers: u.tickers,
      default_period: u.defaultPeriod,
      revision: u.revision,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "user_id,name" },
  );
}

/** Mirror a local universe deletion to Supabase, if signed in. */
export async function syncUniverseDelete(
  userId: string,
  name: string,
): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  await supabase
    .from("universes")
    .delete()
    .eq("user_id", userId)
    .eq("name", name);
}

/**
 * Union-merges the local watchlist with the cloud one: every ticker present
 * on either side ends up on both. Never deletes — a ticker removed locally
 * while signed out (or on another device) isn't silently dropped from the
 * other side by this pass; removeFromWatchlist() below handles explicit
 * removal while signed in.
 */
async function mergeWatchlist(userId: string): Promise<void> {
  if (!supabaseConfigured || !supabase || !db) return;

  const [localItems, cloudResult] = await Promise.all([
    db.watchlist.toArray(),
    supabase.from("watchlist").select("ticker,note,added_at").eq("user_id", userId),
  ]);
  const cloudItems = (cloudResult.data ?? []) as Pick<CloudWatchItem, "ticker" | "note" | "added_at">[];

  const localTickers = new Set(localItems.map((w) => w.ticker));
  const cloudTickers = new Set(cloudItems.map((w) => w.ticker));

  // Push local-only tickers to the cloud.
  const toPush = localItems
    .filter((w) => !cloudTickers.has(w.ticker))
    .map((w) => ({
      user_id: userId,
      ticker: w.ticker,
      note: w.note || null,
    }));
  if (toPush.length > 0) {
    await supabase.from("watchlist").upsert(toPush, { onConflict: "user_id,ticker" });
  }

  // Pull cloud-only tickers down to this device.
  const toPull = cloudItems.filter((w) => !localTickers.has(w.ticker));
  for (const item of toPull) {
    await db.watchlist.put({
      ticker: item.ticker,
      note: item.note ?? "",
      targetPrice: null,
      addedAt: new Date(item.added_at).getTime(),
      lastSignal: null,
      lastCheckedAt: null,
    });
  }
}

/**
 * Mirror a profile field change to Supabase, if signed in. Call alongside
 * (not instead of) db.ts's updateProfile() — Dexie stays authoritative
 * locally, this just keeps the cloud copy current for other devices.
 */
export async function syncProfileToCloud(
  userId: string,
  patch: { name?: string; defaultPeriod?: string; theme?: string }
): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  const update: Record<string, string> = {};
  if (patch.name !== undefined) update.display_name = patch.name;
  if (patch.defaultPeriod !== undefined) update.default_period = patch.defaultPeriod;
  if (patch.theme !== undefined) update.theme = patch.theme;
  if (Object.keys(update).length === 0) return;
  await supabase.from("profiles").update(update).eq("id", userId);
}

/** Mirror a watchlist addition to Supabase, if signed in. */
export async function syncWatchlistAdd(userId: string, ticker: string): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  await supabase
    .from("watchlist")
    .upsert({ user_id: userId, ticker }, { onConflict: "user_id,ticker" });
}

/** Mirror a watchlist removal to Supabase, if signed in. */
export async function syncWatchlistRemove(userId: string, ticker: string): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  await supabase.from("watchlist").delete().eq("user_id", userId).eq("ticker", ticker);
}

/**
 * Delete this user's profile and watchlist rows from Supabase — the cloud
 * half of "forget me" (see SettingsPanel's handleWipe). RLS scopes both
 * deletes to auth.uid(), same as every other per-user write in this module.
 * The `profiles` row's ON DELETE CASCADE from auth.users isn't relied on
 * here since the user isn't being deleted, only their app data.
 */
export async function wipeCloudData(userId: string): Promise<void> {
  if (!supabaseConfigured || !supabase) return;
  await Promise.all([
    supabase.from("watchlist").delete().eq("user_id", userId),
    supabase.from("universes").delete().eq("user_id", userId),
    supabase.from("profiles").delete().eq("id", userId),
  ]);
}
