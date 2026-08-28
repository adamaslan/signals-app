/**
 * Vitest setup: polyfill IndexedDB in the Node test environment so the
 * Dexie-backed db.ts (and universe.ts on top of it) run against a real,
 * queryable store. Each test file gets a fresh in-memory database.
 */
import "fake-indexeddb/auto";
import { beforeEach } from "vitest";
import { db } from "@/lib/db";

beforeEach(async () => {
  if (!db) return;
  await Promise.all(db.tables.map((t) => t.clear()));
});
