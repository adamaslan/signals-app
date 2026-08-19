-- Initial schema for the signals-app engine (Phase 2 of
-- docs/backend-state-and-supabase-plan.md).
--
-- Eight tables: universe + runs, signals (the thing currently thrown away),
-- raw detector output, the validation loop, and user state.

-- ── Universe & runs ────────────────────────────────────────────────
create table symbols (
  ticker        text primary key,
  name          text,
  asset_type    text not null default 'equity',
  active        boolean not null default true,
  priority      int not null default 100,  -- scan order; low = first
  added_at      timestamptz not null default now()
);

create table engine_runs (
  id            bigserial primary key,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  trigger       text not null,             -- 'cron' | 'manual' | 'backfill'
  code_version  text not null,             -- SIGNALS_APP_CODE_VERSION
  git_sha       text not null,
  symbols_total int not null default 0,
  symbols_ok    int not null default 0,
  symbols_failed int not null default 0,
  llm_provider  text,
  status        text not null default 'running',  -- running|ok|partial|failed
  error         text
);

-- ── The thing that is currently thrown away ────────────────────────
create table signals (
  id                  bigserial primary key,
  run_id              bigint not null references engine_runs(id) on delete cascade,
  ticker              text not null references symbols(ticker),
  period              text not null,
  bar_ts              timestamptz not null,   -- the bar this describes
  direction           text,                   -- BUY|SELL|HOLD
  confidence          double precision,
  confluence_score    double precision,
  bias                text,
  bull_count          int,
  bear_count          int,
  total_signals       int,
  data_quality_score  double precision,
  data_quality_reasons jsonb not null default '[]',
  evidence            jsonb not null default '[]',  -- full evidence[]
  counter_evidence    jsonb not null default '[]',
  matrix              jsonb,                  -- TimeframeMatrix when computed
  ai_degraded         boolean not null default false,
  no_llm              boolean not null default false,
  prompt_version      text,
  llm_model           text,
  code_version        text not null,
  created_at          timestamptz not null default now(),
  unique (ticker, period, bar_ts, code_version)
);
create index on signals (ticker, period, bar_ts desc);
create index on signals (created_at desc);

-- ── Raw detector output, for mining and calibration ────────────────
create table detector_hits (
  id           bigserial primary key,
  ticker       text not null references symbols(ticker),
  bar_ts       timestamptz not null,
  detector     text not null,
  category     text not null,
  strength     text not null,
  description  text,
  code_version text not null,
  unique (ticker, bar_ts, detector, description, code_version)
);
create index on detector_hits (ticker, bar_ts desc);
create index on detector_hits (strength, category);

-- ── Validation loop ────────────────────────────────────────────────
create table forward_returns (
  ticker       text not null references symbols(ticker),
  bar_ts       timestamptz not null,
  horizon_days int not null,
  pct_return   double precision not null,
  computed_at  timestamptz not null default now(),
  primary key (ticker, bar_ts, horizon_days)
);

create table calibration (
  id            bigserial primary key,
  computed_at   timestamptz not null default now(),
  code_version  text not null,
  horizon_days  int not null,
  bucket_kind   text not null,   -- 'strength' | 'category' | 'confluence_band'
  bucket_key    text not null,
  hits          int not null,
  total         int not null,
  hit_rate      double precision not null,
  is_active     boolean not null default false
);
create index on calibration (is_active, bucket_kind);

-- ── User state (currently trapped in IndexedDB) ────────────────────
create table profiles (
  id             uuid primary key references auth.users(id) on delete cascade,
  display_name   text,
  default_period text not null default '3mo',
  theme          text not null default 'dark',
  created_at     timestamptz not null default now()
);

create table watchlist (
  user_id  uuid not null references auth.users(id) on delete cascade,
  ticker   text not null references symbols(ticker),
  note     text,
  added_at timestamptz not null default now(),
  primary key (user_id, ticker)
);

-- ── Row-Level Security ──────────────────────────────────────────────
alter table signals        enable row level security;
alter table symbols        enable row level security;
alter table calibration    enable row level security;
alter table engine_runs    enable row level security;
alter table profiles       enable row level security;
alter table watchlist      enable row level security;

-- Public read on engine output. No insert/update policy exists for these —
-- writes happen only through the service-role key (GitHub Actions secrets),
-- which bypasses RLS entirely.
create policy "public read" on signals     for select using (true);
create policy "public read" on symbols     for select using (true);
create policy "public read" on calibration for select using (is_active);
create policy "public read" on engine_runs for select using (true);

-- Per-user rows, owner-only.
create policy "own profile" on profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);
create policy "own watchlist" on watchlist
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- detector_hits and forward_returns get no policies and no grants:
-- internal tables, service-role only.
alter table detector_hits   enable row level security;
alter table forward_returns enable row level security;
