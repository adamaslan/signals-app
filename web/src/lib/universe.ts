/**
 * Local Universes — CRUD, membership, import/export, coverage, tracking, and
 * (cached) backtest helpers over the Dexie tables added in db.ts v2.
 *
 * Mirrors db.ts conventions exactly: every function is SSR-safe (`if (!db)
 * return …`), async, small and single-purpose. A "universe" is a named,
 * device-local basket of tickers; only *reading signals* for a run touches
 * Supabase (the anon public-read path — still no account required).
 *
 * See docs/signals-app-docs/local-universe-save-track-backtest.md.
 */
import {
  db,
  DIRECTION_RANK,
  type Universe,
  type UniverseRun,
  type UniverseRunResult,
  type UniverseRunSummary,
  type UniverseBacktest,
  type HitRateBucketDTO,
} from "./db";
import {
  fetchUniverseSignals,
  fetchCoverage,
  fetchUniverseHitRates,
  fetchUniverseBacktestMeta,
} from "./api";
import { wilsonLowerBound as wilsonLowerBoundImpl } from "./stats";
import type { SignalDirection } from "./types";

/* ────────────────────────────────────────────────────────────────────────
 * Ticker parsing
 * ──────────────────────────────────────────────────────────────────────── */

const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

/** Normalise one raw token to a candidate ticker: strip a leading `$`,
 * uppercase, trim surrounding punctuation. Returns "" if nothing usable. */
function normaliseToken(raw: string): string {
  return raw
    .trim()
    .replace(/^\$/, "")
    .replace(/[^A-Za-z0-9.\-]/g, "")
    .toUpperCase();
}

/**
 * Split arbitrary pasted text into candidate tickers. Newlines, commas,
 * semicolons, pipes and tabs are hard separators. Within one line, tokens
 * are whitespace-separated but truncated at the first numeric token — so
 * `AAPL 100 shares` and `MSFT  20` yield just the leading ticker, while a
 * bare space-separated list like `$aapl msft goog` keeps every symbol.
 */
export function tokenizeTickerText(raw: string): string[] {
  const out: string[] = [];
  for (const line of raw.split(/[\n\r,;|\t]+/)) {
    for (const tok of line.trim().split(/\s+/)) {
      if (!tok) continue;
      // A numeric token (share count, price) ends the ticker part of the line.
      if (/^\$?\d[\d.,]*$/.test(tok)) break;
      out.push(tok);
    }
  }
  return out;
}

/** Parse pasted text into { valid, invalid } ticker buckets (no dedupe). */
export function parseTickerText(raw: string): {
  valid: string[];
  invalid: string[];
} {
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const tok of tokenizeTickerText(raw)) {
    const norm = normaliseToken(tok);
    if (norm && TICKER_RE.test(norm)) valid.push(norm);
    else if (tok) invalid.push(tok);
  }
  return { valid, invalid };
}

/** Uppercase + de-dupe (stable order), dropping anything that fails the
 * ticker shape check. */
export function cleanTickerList(tickers: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of tickers) {
    const norm = normaliseToken(t);
    if (norm && TICKER_RE.test(norm) && !seen.has(norm)) {
      seen.add(norm);
      out.push(norm);
    }
  }
  return out;
}

/* ────────────────────────────────────────────────────────────────────────
 * CRUD
 * ──────────────────────────────────────────────────────────────────────── */

export interface CreateUniverseOpts {
  note?: string;
  tickers?: string[];
  defaultPeriod?: string;
  defaultNoLlm?: boolean;
}

function nameKey(name: string): string {
  return name.trim().toLowerCase();
}

async function assertNameFree(name: string, exceptId?: number): Promise<void> {
  if (!db) return;
  const key = nameKey(name);
  const clash = (await db.universes.toArray()).find(
    (u) => nameKey(u.name) === key && u.id !== exceptId,
  );
  if (clash) throw new Error(`A universe named "${name}" already exists`);
}

/** Create a new universe. Name must be non-empty and unique per device
 * (case-insensitive). */
export async function createUniverse(
  name: string,
  opts: CreateUniverseOpts = {},
): Promise<Universe> {
  const now = Date.now();
  const fresh: Universe = {
    name: name.trim(),
    note: opts.note ?? "",
    tickers: cleanTickerList(opts.tickers ?? []),
    defaultPeriod: opts.defaultPeriod ?? "3mo",
    defaultNoLlm: opts.defaultNoLlm ?? false,
    createdAt: now,
    updatedAt: now,
    revision: 1,
    coverage: null,
  };
  if (!fresh.name) throw new Error("Universe name is required");
  if (!db) return fresh;
  await assertNameFree(fresh.name);
  const id = await db.universes.add(fresh);
  return { ...fresh, id };
}

export async function getUniverse(id: number): Promise<Universe | null> {
  if (!db) return null;
  return (await db.universes.get(id)) ?? null;
}

/** All universes, most-recently-updated first. */
export async function listUniverses(): Promise<Universe[]> {
  if (!db) return [];
  return db.universes.orderBy("updatedAt").reverse().toArray();
}

export async function renameUniverse(id: number, name: string): Promise<void> {
  if (!db) return;
  const trimmed = name.trim();
  if (!trimmed) throw new Error("Universe name is required");
  await assertNameFree(trimmed, id);
  await db.universes.update(id, { name: trimmed, updatedAt: Date.now() });
}

export async function updateUniverseMeta(
  id: number,
  patch: Partial<Pick<Universe, "note" | "defaultPeriod" | "defaultNoLlm">>,
): Promise<void> {
  if (!db) return;
  await db.universes.update(id, { ...patch, updatedAt: Date.now() });
}

/** Delete a universe and cascade its runs + cached backtests. */
export async function deleteUniverse(id: number): Promise<void> {
  if (!db) return;
  const d = db;
  await d.transaction(
    "rw",
    d.universes,
    d.universeRuns,
    d.universeBacktests,
    async () => {
      await d.universes.delete(id);
      await d.universeRuns.where("universeId").equals(id).delete();
      await d.universeBacktests.where("universeId").equals(id).delete();
    },
  );
}

/* ────────────────────────────────────────────────────────────────────────
 * Membership — each mutation bumps revision + updatedAt and invalidates the
 * cached coverage (its ticker set is now stale).
 * ──────────────────────────────────────────────────────────────────────── */

async function mutateTickers(
  id: number,
  fn: (current: string[]) => string[],
): Promise<Universe | null> {
  if (!db) return null;
  const d = db;
  return d.transaction("rw", d.universes, async () => {
    const u = await d.universes.get(id);
    if (!u) throw new Error(`Universe ${id} not found`);
    const next = cleanTickerList(fn(u.tickers));
    const unchanged =
      next.length === u.tickers.length &&
      next.every((t, i) => t === u.tickers[i]);
    if (unchanged) return u;
    const updated: Universe = {
      ...u,
      tickers: next,
      revision: u.revision + 1,
      updatedAt: Date.now(),
      coverage: null,
    };
    await d.universes.put(updated);
    return updated;
  });
}

export async function addTicker(id: number, ticker: string): Promise<void> {
  await mutateTickers(id, (cur) => [...cur, ticker]);
}

export async function removeTicker(id: number, ticker: string): Promise<void> {
  const norm = normaliseToken(ticker);
  await mutateTickers(id, (cur) => cur.filter((t) => t !== norm));
}

export async function setTickers(
  id: number,
  tickers: string[],
): Promise<void> {
  await mutateTickers(id, () => tickers);
}

export interface ImportTickersResult {
  added: string[];
  /** Already in the universe. */
  skipped: string[];
  /** Failed the ticker-shape check. */
  invalid: string[];
}

/** Parse pasted text and merge the valid, new tickers into the universe.
 * Never silently drops anything — the three buckets let the UI report
 * exactly what happened. */
export async function importTickersFromText(
  id: number,
  raw: string,
): Promise<ImportTickersResult> {
  const { valid, invalid } = parseTickerText(raw);
  const cleaned = cleanTickerList(valid);
  if (!db) return { added: cleaned, skipped: [], invalid };

  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);

  const existing = new Set(u.tickers);
  const added = cleaned.filter((t) => !existing.has(t));
  const skipped = cleaned.filter((t) => existing.has(t));

  if (added.length > 0) {
    await mutateTickers(id, (cur) => [...cur, ...added]);
  }
  return { added, skipped, invalid };
}

/* ────────────────────────────────────────────────────────────────────────
 * Import / export — mirrors exportAll/wipeAll in db.ts
 * ──────────────────────────────────────────────────────────────────────── */

export interface UniverseExportDTO {
  exportedAt: string;
  universe: Omit<Universe, "id">;
  runs: Omit<UniverseRun, "id">[];
  backtests: Omit<UniverseBacktest, "id">[];
}

export async function exportUniverse(id: number): Promise<UniverseExportDTO> {
  if (!db) throw new Error("No local database");
  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);
  const [runs, backtests] = await Promise.all([
    db.universeRuns.where("universeId").equals(id).toArray(),
    db.universeBacktests.where("universeId").equals(id).toArray(),
  ]);
  const strip = <T extends { id?: number }>(rows: T[]) =>
    rows.map(({ id: _id, ...rest }) => rest);
  const { id: _uid, ...universe } = u;
  return {
    exportedAt: new Date().toISOString(),
    universe,
    runs: strip(runs),
    backtests: strip(backtests),
  };
}

/** Recreate a universe (and its runs/backtests) from an export. The name is
 * suffixed with " (imported)" if it would clash. */
export async function importUniverse(
  dto: UniverseExportDTO,
): Promise<Universe> {
  if (!db) throw new Error("No local database");
  let name = dto.universe.name;
  const key = nameKey(name);
  const clash = (await db.universes.toArray()).some(
    (u) => nameKey(u.name) === key,
  );
  if (clash) name = `${name} (imported)`;

  const d = db;
  return d.transaction(
    "rw",
    d.universes,
    d.universeRuns,
    d.universeBacktests,
    async () => {
      const id = await d.universes.add({
        ...dto.universe,
        name,
        tickers: cleanTickerList(dto.universe.tickers),
        updatedAt: Date.now(),
      });
      for (const run of dto.runs) {
        await d.universeRuns.add({ ...run, universeId: id });
      }
      for (const bt of dto.backtests) {
        await d.universeBacktests.add({ ...bt, universeId: id });
      }
      const created = await d.universes.get(id);
      return created as Universe;
    },
  );
}

/** CSV: one row per ticker, plus the universe's newest run result if any. */
export async function exportUniverseCsv(id: number): Promise<string> {
  if (!db) return "";
  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);
  const latest = (
    await db.universeRuns
      .where("universeId")
      .equals(id)
      .reverse()
      .sortBy("startedAt")
  )[0];
  const byTicker = new Map<string, UniverseRunResult>(
    (latest?.results ?? []).map((r) => [r.ticker, r]),
  );
  const header = "ticker,signal,confidence,confluence,data_quality,error";
  const lines = u.tickers.map((t) => {
    const r = byTicker.get(t);
    return [
      t,
      r?.signal ?? "",
      r?.confidence ?? "",
      r?.confluenceScore ?? "",
      r?.dataQuality ?? "",
      r?.error ?? "",
    ].join(",");
  });
  return [header, ...lines].join("\n");
}

/* ────────────────────────────────────────────────────────────────────────
 * Coverage (§3.4) — the honest answer to "why is my ticker blank?"
 * ──────────────────────────────────────────────────────────────────────── */

export async function refreshCoverage(
  id: number,
): Promise<Universe["coverage"]> {
  if (!db) return null;
  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);
  const { covered, inactive, uncovered } = await fetchCoverage(u.tickers);
  const coverage: Universe["coverage"] = {
    checkedAt: Date.now(),
    covered,
    inactive,
    uncovered,
  };
  await db.universes.update(id, { coverage });
  return coverage;
}

/* ────────────────────────────────────────────────────────────────────────
 * Tracking — one batched Supabase read per run
 * ──────────────────────────────────────────────────────────────────────── */

const BULLISH = new Set<SignalDirection>(["strong_buy", "buy"]);
const BEARISH = new Set<SignalDirection>(["strong_sell", "sell"]);

function summarise(results: UniverseRunResult[]): UniverseRunSummary {
  let bullish = 0;
  let bearish = 0;
  let neutral = 0;
  let failed = 0;
  let uncovered = 0;
  const conf: number[] = [];
  const dq: number[] = [];
  const align: number[] = [];

  for (const r of results) {
    if (r.error === "uncovered") {
      uncovered += 1;
      continue;
    }
    if (r.error) {
      failed += 1;
      continue;
    }
    if (r.signal && BULLISH.has(r.signal)) bullish += 1;
    else if (r.signal && BEARISH.has(r.signal)) bearish += 1;
    else neutral += 1;
    if (r.confidence != null) conf.push(r.confidence);
    if (r.dataQuality != null) dq.push(r.dataQuality);
    if (r.alignmentScore != null) align.push(r.alignmentScore);
  }
  const mean = (xs: number[]) =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

  return {
    counted: bullish + bearish + neutral,
    bullish,
    bearish,
    neutral,
    failed,
    uncovered,
    avgConfidence: mean(conf),
    avgDataQuality: mean(dq),
    avgAlignment: mean(align),
  };
}

export interface RunUniverseOpts {
  period?: string;
}

/**
 * Refresh a whole universe: one (or a few) batched Supabase reads, one
 * `UniverseRun` row persisted. Tickers with no row are recorded as
 * `error: "uncovered"` — never as a failure or a `hold`.
 */
export async function runUniverse(
  id: number,
  opts: RunUniverseOpts = {},
): Promise<UniverseRun> {
  if (!db) throw new Error("No local database");
  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);

  const period = opts.period ?? u.defaultPeriod;
  const startedAt = Date.now();
  const runId = await db.universeRuns.add({
    universeId: id,
    universeRevision: u.revision,
    period,
    startedAt,
    finishedAt: null,
    status: "running",
    results: [],
    summary: null,
  });

  let results: UniverseRunResult[];
  let status: UniverseRun["status"];
  try {
    const snapshots = await fetchUniverseSignals(u.tickers, period);
    results = u.tickers.map((ticker) => {
      const s = snapshots.get(ticker);
      if (!s) {
        return {
          ticker,
          signal: null,
          confidence: null,
          confluenceScore: null,
          dataQuality: null,
          alignmentScore: null,
          divergencePattern: null,
          aiDegraded: false,
          barTs: null,
          codeVersion: null,
          error: "uncovered",
        };
      }
      return {
        ticker,
        signal: s.direction,
        confidence: s.confidence,
        confluenceScore: s.confluenceScore,
        dataQuality: s.dataQuality,
        alignmentScore: s.alignmentScore,
        divergencePattern: s.divergencePattern,
        aiDegraded: s.aiDegraded,
        barTs: s.barTs,
        codeVersion: s.codeVersion,
        error: null,
      };
    });
    const anyUncovered = results.some((r) => r.error === "uncovered");
    const anyCovered = results.some((r) => r.error == null);
    status = anyUncovered && anyCovered ? "partial" : anyCovered ? "complete" : "partial";
  } catch (err) {
    const message = err instanceof Error ? err.message : "run failed";
    results = u.tickers.map((ticker) => ({
      ticker,
      signal: null,
      confidence: null,
      confluenceScore: null,
      dataQuality: null,
      alignmentScore: null,
      divergencePattern: null,
      aiDegraded: false,
      barTs: null,
      codeVersion: null,
      error: message,
    }));
    status = "failed";
  }

  const summary = summarise(results);
  const finishedAt = Date.now();
  await db.universeRuns.update(runId, {
    finishedAt,
    status,
    results,
    summary,
  });
  return (await db.universeRuns.get(runId)) as UniverseRun;
}

export async function listUniverseRuns(
  id: number,
  limit = 50,
): Promise<UniverseRun[]> {
  if (!db) return [];
  const rows = await db.universeRuns
    .where("universeId")
    .equals(id)
    .reverse()
    .sortBy("startedAt");
  return rows.slice(0, limit);
}

export async function getUniverseRun(
  runId: number,
): Promise<UniverseRun | null> {
  if (!db) return null;
  return (await db.universeRuns.get(runId)) ?? null;
}

/* ────────────────────────────────────────────────────────────────────────
 * diffRuns — the "what changed" view
 * ──────────────────────────────────────────────────────────────────────── */

export type DriftClass =
  | "upgraded"
  | "downgraded"
  | "unchanged"
  | "new"
  | "dropped"
  | "newly-covered"
  | "went-stale";

export interface DriftEntry {
  ticker: string;
  class: DriftClass;
  prevSignal: SignalDirection | null;
  nextSignal: SignalDirection | null;
}

export interface UniverseDriftDTO {
  prevRunId: number;
  nextRunId: number;
  /** True when the two runs were taken against different memberships — a
   * membership edit would then masquerade as market movement. */
  revisionMismatch: boolean;
  prevRevision: number;
  nextRevision: number;
  entries: DriftEntry[];
}

function classifyDrift(
  prev: UniverseRunResult | undefined,
  next: UniverseRunResult | undefined,
): DriftClass {
  const prevCovered = prev != null && prev.error == null;
  const nextCovered = next != null && next.error == null;

  if (!prev && next) return "new";
  if (prev && !next) return "dropped";
  if (!prevCovered && nextCovered) return "newly-covered";
  if (prevCovered && !nextCovered) return "went-stale";
  if (!prevCovered && !nextCovered) return "unchanged";

  const p = prev!.signal ? DIRECTION_RANK[prev!.signal] : 0;
  const n = next!.signal ? DIRECTION_RANK[next!.signal] : 0;
  if (n > p) return "upgraded";
  if (n < p) return "downgraded";
  return "unchanged";
}

/** Compare two runs ticker-by-ticker. Does NOT throw on a revision
 * mismatch — it flags it (`revisionMismatch`) so the UI can show a banner
 * rather than silently conflating membership edits with market movement. */
export async function diffRuns(
  prevRunId: number,
  nextRunId: number,
): Promise<UniverseDriftDTO> {
  if (!db) throw new Error("No local database");
  const [prev, next] = await Promise.all([
    db.universeRuns.get(prevRunId),
    db.universeRuns.get(nextRunId),
  ]);
  if (!prev || !next) throw new Error("Run not found");

  const prevByTicker = new Map(prev.results.map((r) => [r.ticker, r]));
  const nextByTicker = new Map(next.results.map((r) => [r.ticker, r]));
  const allTickers = new Set([...prevByTicker.keys(), ...nextByTicker.keys()]);

  const entries: DriftEntry[] = [];
  for (const ticker of allTickers) {
    const p = prevByTicker.get(ticker);
    const n = nextByTicker.get(ticker);
    entries.push({
      ticker,
      class: classifyDrift(p, n),
      prevSignal: p?.signal ?? null,
      nextSignal: n?.signal ?? null,
    });
  }
  entries.sort((a, b) => a.ticker.localeCompare(b.ticker));

  return {
    prevRunId,
    nextRunId,
    revisionMismatch: prev.universeRevision !== next.universeRevision,
    prevRevision: prev.universeRevision,
    nextRevision: next.universeRevision,
    entries,
  };
}

/* ────────────────────────────────────────────────────────────────────────
 * Backtest — cached; computed via the universe_hit_rates /
 * universe_backtest_meta RPCs (migration 20260831000002).
 * ──────────────────────────────────────────────────────────────────────── */

// Re-exported for call-site / test compatibility; the implementations now
// live in ./stats so the backtest panel can import them without pulling in
// the whole universe module.
export { wilsonLowerBound, wilsonUpperBound } from "./stats";

/** @deprecated use `toHitRateBucket` from ./stats (this drops the upper bound). */
export function toBucketDTO(
  key: string,
  hits: number,
  total: number,
): HitRateBucketDTO {
  return {
    key,
    hits,
    total,
    hitRate: total ? hits / total : 0,
    hitRateLower: wilsonLowerBoundImpl(hits, total),
  };
}

export async function getCachedUniverseBacktest(
  id: number,
  horizonDays: number,
): Promise<UniverseBacktest | null> {
  if (!db) return null;
  const u = await db.universes.get(id);
  if (!u) return null;
  const hit = await db.universeBacktests
    .where("[universeId+universeRevision+horizonDays]")
    .equals([id, u.revision, horizonDays])
    .first();
  return hit ?? null;
}

function dtoFromRaw(
  b: { bucket_key: string; hits: number; total: number },
): HitRateBucketDTO {
  return {
    key: b.bucket_key,
    hits: b.hits,
    total: b.total,
    hitRate: b.total ? b.hits / b.total : 0,
    hitRateLower: wilsonLowerBoundImpl(b.hits, b.total),
  };
}

export interface BacktestUniverseOpts {
  /** Skip the cache and recompute. */
  force?: boolean;
}

/**
 * Backtest a universe over `horizonDays`. Returns a cached row when one
 * exists for the current membership revision + horizon (unless `force`);
 * otherwise calls the `universe_hit_rates` RPC three times (by strength,
 * category, ticker) plus `universe_backtest_meta`, assembles a
 * `UniverseBacktest`, caches it, and returns it.
 *
 * Throws an ApiError(501) with an actionable message when the RPC migration
 * hasn't been applied — never silently returns empty buckets.
 */
export async function backtestUniverse(
  id: number,
  horizonDays: number,
  opts: BacktestUniverseOpts = {},
): Promise<UniverseBacktest> {
  if (!db) throw new Error("No local database");
  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);

  if (!opts.force) {
    const cached = await getCachedUniverseBacktest(id, horizonDays);
    if (cached) return cached;
  }

  const [byStrengthRaw, byCategoryRaw, byTickerRaw, meta] = await Promise.all([
    fetchUniverseHitRates(u.tickers, horizonDays, "strength"),
    fetchUniverseHitRates(u.tickers, horizonDays, "category"),
    fetchUniverseHitRates(u.tickers, horizonDays, "ticker"),
    fetchUniverseBacktestMeta(u.tickers, horizonDays),
  ]);

  const record: UniverseBacktest = {
    universeId: id,
    universeRevision: u.revision,
    horizonDays,
    ranAt: Date.now(),
    byStrength: byStrengthRaw.map(dtoFromRaw),
    byCategory: byCategoryRaw.map(dtoFromRaw),
    byTicker: byTickerRaw.map(dtoFromRaw),
    tickersScored: meta.tickersScored,
    tickersRequested: u.tickers.length,
    hitsTotal: meta.hitsTotal,
    signalsTotal: meta.signalsTotal,
    baselineUpRate: meta.baselineUpRate,
  };

  // Replace any stale row for this exact key in a single transaction.
  const btId = await db.transaction("rw", db.universeBacktests, async () => {
    await db.universeBacktests
      .where("[universeId+universeRevision+horizonDays]")
      .equals([id, u.revision, horizonDays])
      .delete();
    return db.universeBacktests.add(record);
  });
  return { ...record, id: btId };
}

/* ────────────────────────────────────────────────────────────────────────
 * Bridges to the flat watchlist (kept as-is; two one-way helpers)
 * ──────────────────────────────────────────────────────────────────────── */

/** Snapshot the current flat watchlist into a new universe. */
export async function universeFromWatchlist(name: string): Promise<Universe> {
  if (!db) return createUniverse(name);
  const items = await db.watchlist.toArray();
  return createUniverse(name, { tickers: items.map((w) => w.ticker) });
}

/** Add every ticker in a universe to the flat watchlist (idempotent). */
export async function watchlistFromUniverse(id: number): Promise<number> {
  if (!db) return 0;
  const u = await db.universes.get(id);
  if (!u) throw new Error(`Universe ${id} not found`);
  let added = 0;
  for (const ticker of u.tickers) {
    const existing = await db.watchlist.get(ticker);
    if (existing) continue;
    await db.watchlist.put({
      ticker,
      note: "",
      targetPrice: null,
      addedAt: Date.now(),
      lastSignal: null,
      lastCheckedAt: null,
    });
    added += 1;
  }
  return added;
}
