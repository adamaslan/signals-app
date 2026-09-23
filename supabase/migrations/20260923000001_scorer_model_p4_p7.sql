-- Migration: 2026-09-23
-- Purpose: docs/scoring-2x-plan.md P4 + P7.
--   P4: signals gain rank_pct (0-100 percentile within the scan run) and
--       p_outperform (calibrated P(excess return > 0)), plus the model that
--       produced them and its expected excess return.
--   P7: calibration rows carry the model_version they were generated under; the
--       weekly retrain stores its artifact in scorer_models (one active row),
--       and the drift check logs live-vs-backtest IC in scorer_ic_history.
-- All additions are nullable / new, so existing writers and readers keep working.

alter table signals
  add column if not exists rank_pct        double precision check (rank_pct between 0 and 100),
  add column if not exists p_outperform    double precision check (p_outperform between 0 and 1),
  add column if not exists expected_excess double precision,
  add column if not exists model_version   text;

-- `select *` in a view is expanded at creation time; recreate so the new
-- columns are exposed. They are appended, so existing column positions hold.
create or replace view latest_signals as
select distinct on (ticker, period) *
from signals
order by ticker, period, bar_ts desc, created_at desc;

alter table calibration
  add column if not exists model_version text;

create table if not exists scorer_models (
  id            bigserial primary key,
  model_version text not null unique,
  horizon_days  int not null,
  trained_at    timestamptz not null default now(),
  artifact      jsonb not null,     -- LogisticScorer.to_dict()
  metrics       jsonb not null default '{}',
  is_active     boolean not null default false
);
create unique index if not exists scorer_models_one_active
  on scorer_models (horizon_days) where is_active;

create table if not exists scorer_ic_history (
  week_start    date not null,
  model_version text not null,
  live_ic       double precision,   -- realized rank IC of published p_outperform vs 20d excess
  backtest_ic   double precision not null,
  n_dates       int not null default 0,
  n_names       int not null default 0,
  created_at    timestamptz not null default now(),
  primary key (week_start, model_version)
);

-- Internal tables: RLS on, no policy => only the service role reads/writes.
alter table scorer_models      enable row level security;
alter table scorer_ic_history  enable row level security;
