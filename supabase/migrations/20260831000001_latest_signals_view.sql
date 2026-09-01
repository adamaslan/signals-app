-- Migration: 2026-08-31
-- Purpose: a plain (non-materialized) view exposing the newest signal row per
-- (ticker, period) via DISTINCT ON. Replaces per-ticker
-- `.order(created_at desc).limit(1)` reads and the client-side Map de-dup in
-- web/src/lib/api.ts (fetchUniverseSignals) with one indexed query. The
-- existing index on signals (ticker, period, bar_ts desc) backs the ORDER BY.

create or replace view latest_signals as
select distinct on (ticker, period) *
from signals
order by ticker, period, bar_ts desc, created_at desc;

-- Grant SELECT to browser-facing roles (anon and authenticated)
grant select on latest_signals to anon, authenticated;

-- Enable security_invoker on the view so that row-level security policies
-- from the underlying signals table are enforced for the querying role.
-- This ensures the `public read` policy is applied to both anon and authenticated users.
alter view latest_signals set (security_invoker = on);
