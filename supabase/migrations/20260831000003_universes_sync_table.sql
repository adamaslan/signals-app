-- Migration: universes_sync_table
-- Date: 2026-08-31
-- Purpose: Create cloud mirror of device-local Dexie `universes` table for cross-device sync.
--
-- This table syncs user-curated ticker universes across devices for signed-in users.
-- Runs and backtest caches remain local-only per design.
-- Note: `tickers` array deliberately has no FK to symbols; users may save uncovered tickers
-- and see them flagged rather than have the insert rejected.

create table universes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users on delete cascade,
  name text not null,
  note text not null default '',
  tickers text[] not null default '{}',
  default_period text not null default '3mo',
  revision integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, name)
);

create index on universes (user_id, updated_at desc);

alter table universes enable row level security;

create policy "own universes" on universes
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

grant select, insert, update, delete on universes to authenticated;
