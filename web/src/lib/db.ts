/**
 * On-device local database for the Signals web app.
 *
 * Backed by IndexedDB via Dexie — a real, queryable, indexed store that lives
 * entirely on the user's machine (phone or PC). No data leaves the device and
 * no server-side database is involved. Survives refreshes, tab closes, and
 * restarts.
 *
 * A small cookie (see cookies.ts) mirrors a couple of fields (name + last
 * ticker) so the server can render a personalised first paint before this
 * client-only DB has hydrated.
 */
import Dexie, { type Table } from "dexie";
import type { SignalDirection } from "./types";

/** Single-row user profile (id is always PROFILE_ID). */
export interface Profile {
  id: string;
  name: string;
  /** Preferred UI period id (may be an unsupported-but-mapped id like "1h"). */
  defaultPeriod: string;
  /** Default "rule-based only" preference. */
  defaultNoLlm: boolean;
  /** Colour theme preference. */
  theme: "dark" | "midnight" | "system";
  /** Epoch ms of first app use. */
  firstSeen: number;
  /** Epoch ms of most recent app use. */
  lastSeen: number;
  /** Most recently analysed ticker. */
  lastTicker: string | null;
  /** Most recent signal operation (direction) for lastTicker. */
  lastSignal: SignalDirection | null;
  /** Lifetime count of analyses run on this device. */
  totalRuns: number;
}

/** One persisted analysis run. */
export interface HistoryEntry {
  id?: number;
  ticker: string;
  /** UI period id used (e.g. "3mo", "1h"). */
  period: string;
  /** Backend period actually queried after fallback mapping. */
  resolvedPeriod: string;
  noLlm: boolean;
  /** Resulting signal direction, or null if the run failed. */
  signal: SignalDirection | null;
  /** Confidence 0–1, or null if unavailable. */
  confidence: number | null;
  /** True when the AI fell back to rule-based. */
  aiDegraded: boolean;
  /** Epoch ms of the run. */
  ts: number;
}

/** A starred ticker the user is tracking. */
export interface WatchItem {
  ticker: string;
  /** Optional free-text note (thesis, target, etc.). */
  note: string;
  /** Optional price target the user is watching for. */
  targetPrice: number | null;
  /** Epoch ms added. */
  addedAt: number;
  /** Last signal seen for this ticker, for at-a-glance status in the list. */
  lastSignal: SignalDirection | null;
  lastCheckedAt: number | null;
}

/** A named, reusable analysis configuration preset. */
export interface SavedConfig {
  id?: number;
  name: string;
  period: string;
  noLlm: boolean;
  createdAt: number;
}

/**
 * An alert rule: notify when `ticker` reaches `direction` (or stronger toward
 * that side). Evaluated against each fresh run for that ticker.
 */
export interface AlertRule {
  id?: number;
  ticker: string;
  /** Target direction that should trigger the alert. */
  direction: SignalDirection;
  /** Minimum confidence required to trigger (0–1). */
  minConfidence: number;
  enabled: boolean;
  createdAt: number;
  /** Epoch ms it last fired, null if never. */
  lastFiredAt: number | null;
}

/* ────────────────────────────────────────────────────────────────────────
 * Local Universes (v2) — a named, user-owned basket of tickers that can be
 * tracked over time and backtested. See
 * docs/signals-app-docs/local-universe-save-track-backtest.md. All three
 * tables are device-local; only *reading signals* touches Supabase.
 * ──────────────────────────────────────────────────────────────────────── */

/** A named, user-owned basket of tickers. The core new object. */
export interface Universe {
  id?: number;
  /** Unique per device (case-insensitive), enforced in the helper. */
  name: string;
  /** Thesis for the whole basket. */
  note: string;
  /** Uppercased, de-duped on write. */
  tickers: string[];
  defaultPeriod: string;
  defaultNoLlm: boolean;
  createdAt: number;
  updatedAt: number;
  /** Bumped on every membership change, so a run records what it ran against. */
  revision: number;
  /** Cached coverage check — which tickers the scanner actually has. */
  coverage: {
    checkedAt: number;
    covered: string[];
    /** Known to `symbols` but `active = false`. */
    inactive: string[];
    /** Not in `symbols` at all — will always render blank. */
    uncovered: string[];
  } | null;
}

/** One batch refresh of a whole universe. */
export interface UniverseRun {
  id?: number;
  universeId: number;
  /** Membership snapshot at run time. */
  universeRevision: number;
  period: string;
  startedAt: number;
  finishedAt: number | null;
  status: "running" | "complete" | "partial" | "failed";
  /** Denormalised for one-read rendering. */
  results: UniverseRunResult[];
  summary: UniverseRunSummary | null;
}

export interface UniverseRunSummary {
  counted: number;
  bullish: number;
  bearish: number;
  neutral: number;
  failed: number;
  uncovered: number;
  avgConfidence: number | null;
  avgDataQuality: number | null;
  /** Mean cross-timeframe alignment across the basket. */
  avgAlignment: number | null;
}

export interface UniverseRunResult {
  ticker: string;
  signal: SignalDirection | null;
  confidence: number | null;
  confluenceScore: number | null;
  dataQuality: number | null;
  alignmentScore: number | null;
  divergencePattern: string | null;
  aiDegraded: boolean;
  /** The bar this signal describes — NOT when we fetched it. */
  barTs: number | null;
  codeVersion: string | null;
  /** null = fine; "uncovered" = scanner has no row; else an error message. */
  error: string | null;
}

/** A cached universe-level backtest. Expensive to compute, so we keep it. */
export interface UniverseBacktest {
  id?: number;
  universeId: number;
  universeRevision: number;
  horizonDays: number;
  ranAt: number;
  byStrength: HitRateBucketDTO[];
  byCategory: HitRateBucketDTO[];
  /** Per-ticker contribution, so the UI can show who carried the number. */
  byTicker: HitRateBucketDTO[];
  tickersScored: number;
  tickersRequested: number;
  hitsTotal: number;
  signalsTotal: number;
  /** Baseline: unconditional up-rate over the same bars. */
  baselineUpRate: number | null;
}

export interface HitRateBucketDTO {
  key: string;
  hits: number;
  total: number;
  hitRate: number;
  /** Wilson 95% lower bound — the honest number. */
  hitRateLower: number;
}

export const PROFILE_ID = "me";

class SignalsDB extends Dexie {
  profile!: Table<Profile, string>;
  history!: Table<HistoryEntry, number>;
  watchlist!: Table<WatchItem, string>;
  savedConfigs!: Table<SavedConfig, number>;
  alerts!: Table<AlertRule, number>;
  universes!: Table<Universe, number>;
  universeRuns!: Table<UniverseRun, number>;
  universeBacktests!: Table<UniverseBacktest, number>;

  constructor() {
    super("signals_app");
    this.version(1).stores({
      profile: "id",
      history: "++id, ticker, ts, signal",
      watchlist: "ticker, addedAt, lastSignal",
      savedConfigs: "++id, name, createdAt",
      alerts: "++id, ticker, enabled, createdAt",
    });
    // v2: add the three local-universe tables. New tables only — Dexie
    // creates them with no upgrade() callback (only populated-table
    // *reshapes* need one). The compound index on universeBacktests is the
    // cache key — check it before recomputing.
    this.version(2).stores({
      profile: "id",
      history: "++id, ticker, ts, signal",
      watchlist: "ticker, addedAt, lastSignal",
      savedConfigs: "++id, name, createdAt",
      alerts: "++id, ticker, enabled, createdAt",
      universes: "++id, name, updatedAt",
      universeRuns: "++id, universeId, startedAt, status",
      universeBacktests:
        "++id, universeId, ranAt, [universeId+universeRevision+horizonDays]",
    });
  }
}

/**
 * Guard so this module is import-safe during SSR (no IndexedDB on the
 * server). We key off `indexedDB` availability rather than `window` so that
 * a test runner which polyfills `indexedDB` (fake-indexeddb) gets a real,
 * queryable store.
 */
function createDb(): SignalsDB | null {
  if (typeof indexedDB === "undefined") return null;
  return new SignalsDB();
}

export const db = createDb();

/* ────────────────────────────────────────────────────────────────────────
 * Profile helpers
 * ──────────────────────────────────────────────────────────────────────── */

function defaultProfile(name: string): Profile {
  const now = Date.now();
  return {
    id: PROFILE_ID,
    name,
    defaultPeriod: "3mo",
    defaultNoLlm: false,
    theme: "dark",
    firstSeen: now,
    lastSeen: now,
    lastTicker: null,
    lastSignal: null,
    totalRuns: 0,
  };
}

/** Read the profile, or null if none exists yet (and not on the server). */
export async function getProfile(): Promise<Profile | null> {
  if (!db) return null;
  return (await db.profile.get(PROFILE_ID)) ?? null;
}

/** Create the profile on first use; no-op if it already exists. */
export async function ensureProfile(name: string): Promise<Profile> {
  if (!db) return defaultProfile(name);
  const existing = await db.profile.get(PROFILE_ID);
  if (existing) {
    await db.profile.update(PROFILE_ID, { lastSeen: Date.now() });
    return { ...existing, lastSeen: Date.now() };
  }
  const fresh = defaultProfile(name);
  await db.profile.put(fresh);
  return fresh;
}

export async function updateProfile(patch: Partial<Profile>): Promise<void> {
  if (!db) return;
  await db.profile.update(PROFILE_ID, patch);
}

/* ────────────────────────────────────────────────────────────────────────
 * Run recording — the core "last used / last ticker / last signal" path
 * ──────────────────────────────────────────────────────────────────────── */

export interface RecordRunInput {
  ticker: string;
  period: string;
  resolvedPeriod: string;
  noLlm: boolean;
  signal: SignalDirection | null;
  confidence: number | null;
  aiDegraded: boolean;
}

/**
 * Persist an analysis run: appends to history, bumps the profile's
 * last-used/last-ticker/last-signal + totalRuns, refreshes any watchlist row,
 * and returns the alerts that fired for this result.
 */
export async function recordRun(input: RecordRunInput): Promise<AlertRule[]> {
  if (!db) return [];
  const ts = Date.now();

  await db.history.add({ ...input, ts });

  await db.profile.update(PROFILE_ID, {
    lastSeen: ts,
    lastTicker: input.ticker,
    lastSignal: input.signal,
  });
  // totalRuns is a counter — read/modify/write inside a txn for correctness.
  await db.transaction("rw", db.profile, async () => {
    const p = await db.profile.get(PROFILE_ID);
    if (p) await db.profile.update(PROFILE_ID, { totalRuns: p.totalRuns + 1 });
  });

  const watched = await db.watchlist.get(input.ticker);
  if (watched) {
    await db.watchlist.update(input.ticker, {
      lastSignal: input.signal,
      lastCheckedAt: ts,
    });
  }

  return evaluateAlerts(input.ticker, input.signal, input.confidence, ts);
}

/** Recent runs, newest first. */
export async function getRecentRuns(limit = 25): Promise<HistoryEntry[]> {
  if (!db) return [];
  return db.history.orderBy("ts").reverse().limit(limit).toArray();
}

/** Runs for a single ticker, newest first — powers the per-ticker sparkline. */
export async function getTickerHistory(
  ticker: string,
  limit = 50,
): Promise<HistoryEntry[]> {
  if (!db) return [];
  return db.history
    .where("ticker")
    .equals(ticker)
    .reverse()
    .sortBy("ts")
    .then((rows) => rows.slice(0, limit));
}

export async function clearHistory(): Promise<void> {
  if (!db) return;
  await db.history.clear();
}

/* ────────────────────────────────────────────────────────────────────────
 * Watchlist
 * ──────────────────────────────────────────────────────────────────────── */

export async function addToWatchlist(ticker: string): Promise<void> {
  if (!db) return;
  const existing = await db.watchlist.get(ticker);
  if (existing) return;
  await db.watchlist.put({
    ticker,
    note: "",
    targetPrice: null,
    addedAt: Date.now(),
    lastSignal: null,
    lastCheckedAt: null,
  });
}

export async function removeFromWatchlist(ticker: string): Promise<void> {
  if (!db) return;
  await db.watchlist.delete(ticker);
}

export async function updateWatchItem(
  ticker: string,
  patch: Partial<WatchItem>,
): Promise<void> {
  if (!db) return;
  await db.watchlist.update(ticker, patch);
}

export async function isWatched(ticker: string): Promise<boolean> {
  if (!db) return false;
  return (await db.watchlist.get(ticker)) != null;
}

/* ────────────────────────────────────────────────────────────────────────
 * Saved configs (presets)
 * ──────────────────────────────────────────────────────────────────────── */

export async function saveConfig(
  name: string,
  period: string,
  noLlm: boolean,
): Promise<void> {
  if (!db) return;
  await db.savedConfigs.add({ name, period, noLlm, createdAt: Date.now() });
}

export async function deleteConfig(id: number): Promise<void> {
  if (!db) return;
  await db.savedConfigs.delete(id);
}

/* ────────────────────────────────────────────────────────────────────────
 * Alerts
 * ──────────────────────────────────────────────────────────────────────── */

/** Rank used to compare "X or stronger toward the same side". */
export const DIRECTION_RANK: Record<SignalDirection, number> = {
  strong_sell: -2,
  sell: -1,
  hold: 0,
  buy: 1,
  strong_buy: 2,
};

export async function addAlert(
  ticker: string,
  direction: SignalDirection,
  minConfidence: number,
): Promise<void> {
  if (!db) return;
  await db.alerts.add({
    ticker,
    direction,
    minConfidence,
    enabled: true,
    createdAt: Date.now(),
    lastFiredAt: null,
  });
}

export async function deleteAlert(id: number): Promise<void> {
  if (!db) return;
  await db.alerts.delete(id);
}

export async function toggleAlert(id: number, enabled: boolean): Promise<void> {
  if (!db) return;
  await db.alerts.update(id, { enabled });
}

/**
 * Check enabled alerts for a ticker against a fresh result. An alert fires
 * when the result direction is at least as extreme as the target (same side)
 * and confidence clears the threshold. Returns the rules that fired.
 */
async function evaluateAlerts(
  ticker: string,
  signal: SignalDirection | null,
  confidence: number | null,
  ts: number,
): Promise<AlertRule[]> {
  if (!db || signal == null) return [];
  const rules = await db.alerts
    .where("ticker")
    .equals(ticker)
    .filter((r) => r.enabled)
    .toArray();

  const fired: AlertRule[] = [];
  for (const rule of rules) {
    const target = DIRECTION_RANK[rule.direction];
    const got = DIRECTION_RANK[signal];
    const sameSide = target === 0 ? got === 0 : Math.sign(target) === Math.sign(got);
    const strongEnough = Math.abs(got) >= Math.abs(target);
    const confOk = (confidence ?? 0) >= rule.minConfidence;
    if (sameSide && strongEnough && confOk) {
      fired.push(rule);
      if (rule.id != null) await db.alerts.update(rule.id, { lastFiredAt: ts });
    }
  }
  return fired;
}

/* ────────────────────────────────────────────────────────────────────────
 * Export / wipe (data ownership)
 * ──────────────────────────────────────────────────────────────────────── */

/** Dump the entire local DB as a JSON-serialisable object for export. */
export async function exportAll(): Promise<Record<string, unknown>> {
  if (!db) return {};
  const [
    profile,
    history,
    watchlist,
    savedConfigs,
    alerts,
    universes,
    universeRuns,
    universeBacktests,
  ] = await Promise.all([
    db.profile.toArray(),
    db.history.toArray(),
    db.watchlist.toArray(),
    db.savedConfigs.toArray(),
    db.alerts.toArray(),
    db.universes.toArray(),
    db.universeRuns.toArray(),
    db.universeBacktests.toArray(),
  ]);
  return {
    exportedAt: new Date().toISOString(),
    profile,
    history,
    watchlist,
    savedConfigs,
    alerts,
    universes,
    universeRuns,
    universeBacktests,
  };
}

/** Erase every table — the "forget me" button. */
export async function wipeAll(): Promise<void> {
  if (!db) return;
  await Promise.all([
    db.profile.clear(),
    db.history.clear(),
    db.watchlist.clear(),
    db.savedConfigs.clear(),
    db.alerts.clear(),
    db.universes.clear(),
    db.universeRuns.clear(),
    db.universeBacktests.clear(),
  ]);
}
