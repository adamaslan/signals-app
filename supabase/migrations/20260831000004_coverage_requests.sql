-- Coverage request queue
-- Purpose: Turn the "uncovered ticker → always blank" dead-end into a demand signal.
-- When a user adds an uncovered ticker, they can request it for the operator to add.
-- The operator reads pending rows and appends them to the seed universe.
-- Gives the operator real demand data instead of guessing.
-- Date: 2026-08-31

create table coverage_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users on delete cascade,
  ticker text not null,
  note text not null default '',
  status text not null default 'pending',   -- pending | added | rejected
  requested_at timestamptz not null default now(),
  resolved_at timestamptz,
  unique (user_id, ticker)
);

create index on coverage_requests (status, requested_at);

alter table coverage_requests enable row level security;

create policy "insert own coverage requests" on coverage_requests for insert to authenticated with check (auth.uid() = user_id);

create policy "read own coverage requests" on coverage_requests for select to authenticated using (auth.uid() = user_id);

grant select, insert on coverage_requests to authenticated;
