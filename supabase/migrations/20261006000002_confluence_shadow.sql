-- P6 (docs/states-and-near-a-level-as-signals-2026-10-06.md §8.3): shadow scores
-- from the graded confluence ranker, one row per scored (ticker, bar), so the
-- weekly shadow analysis can join them to forward_returns.
--
-- A new table rather than a column on `signals`: `signals` only holds rows that
-- cleared the publication gate, but the flips worth measuring (HOLD->BUY and
-- BUY->HOLD) include symbols the old ranker gated out.
--
-- Additive only: creates one table and one index. Nothing reads it in
-- production; writes stay off until SIGNALS_WRITE_CONFLUENCE_SHADOW=1.
create table if not exists confluence_shadow (
  id             bigserial primary key,
  ticker         text not null references symbols(ticker),
  bar_ts         timestamptz not null,
  code_version   text not null,
  ranker_version text not null,
  score          double precision not null,
  action         text not null,
  payload        jsonb not null,
  created_at     timestamptz not null default now(),
  unique (ticker, bar_ts, code_version, ranker_version)
);
create index if not exists confluence_shadow_ticker_bar_idx on confluence_shadow (ticker, bar_ts desc);
